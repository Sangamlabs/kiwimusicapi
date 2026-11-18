use reqwest::multipart;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;
use std::time::Duration;
use thiserror::Error;
use tracing::{info, warn};

use crate::security::validate_url_ssrf;

#[derive(Error, Debug, Clone, PartialEq)]
pub enum StorageError {
    #[error("Object not found on storage: {0}")]
    NotFound(String),

    #[error("Yukysbox HTTP error: status={status}, message={message}")]
    Http { status: u16, message: String },

    #[error("Network error: {0}")]
    Network(String),

    #[error("Request timed out after {0}s")]
    Timeout(u64),

    #[error("Security violation (SSRF): {0}")]
    Security(String),

    #[error("Upload failed: {0}")]
    UploadFailed(String),

    #[error("Verification failed: object {uid} size is {size} <= 0")]
    VerificationFailed { uid: String, size: i64 },

    #[error("I/O error: {0}")]
    Io(String),
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct UploadResult {
    pub uid: String,
    pub url: String,
    pub file_size: Option<i64>,
    pub reused: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct YukisboxFileInfo {
    pub uid: String,
    pub size: i64,
    pub filename: Option<String>,
    pub content_type: Option<String>,
}

#[derive(Deserialize)]
#[allow(dead_code)]
struct UploadApiResponse {
    pub success: Option<bool>,
    pub uid: Option<String>,
    pub url: Option<String>,
    pub error: Option<String>,
    pub message: Option<String>,
}

#[derive(Deserialize)]
struct FileInfoApiResponse {
    pub uid: Option<String>,
    pub size: Option<i64>,
    pub filename: Option<String>,
    pub content_type: Option<String>,
}

pub fn sanitize_slug(text: &str) -> String {
    let mut slug = String::with_capacity(text.len());
    let mut last_was_dash = true;

    for c in text.chars() {
        if c.is_ascii_alphanumeric() {
            slug.push(c.to_ascii_lowercase());
            last_was_dash = false;
        } else if !last_was_dash {
            slug.push('-');
            last_was_dash = true;
        }
    }

    // Trim trailing dashes
    while slug.ends_with('-') {
        slug.pop();
    }

    if slug.len() > 80 {
        slug.truncate(80);
        while slug.ends_with('-') {
            slug.pop();
        }
    }

    slug
}

#[derive(Clone)]
pub struct YukisboxStorage {
    base_url: String,
    timeout: Duration,
    client: reqwest::Client,
}

impl YukisboxStorage {
    pub fn new(base_url: Option<String>, timeout_secs: u64) -> Result<Self, StorageError> {
        let base = base_url
            .unwrap_or_else(|| "https://yukiapi.site".to_string())
            .trim_end_matches('/')
            .to_string();

        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(timeout_secs))
            .build()
            .map_err(|e| StorageError::Network(e.to_string()))?;

        Ok(Self {
            base_url: base,
            timeout: Duration::from_secs(timeout_secs),
            client,
        })
    }

    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    /// Query object metadata from GET /v1/file/{uid}/info
    pub async fn get_file_info(&self, uid: &str) -> Result<Option<YukisboxFileInfo>, StorageError> {
        if uid.trim().is_empty() {
            return Ok(None);
        }

        let clean_uid = sanitize_slug(uid);
        let url = format!("{}/v1/file/{}/info", self.base_url, clean_uid);

        let resp = match self.client.get(&url).send().await {
            Ok(r) => r,
            Err(e) => {
                if e.is_timeout() {
                    return Err(StorageError::Timeout(self.timeout.as_secs()));
                }
                return Err(StorageError::Network(e.to_string()));
            }
        };

        if resp.status() == reqwest::StatusCode::NOT_FOUND {
            return Ok(None);
        }

        if !resp.status().is_success() {
            return Err(StorageError::Http {
                status: resp.status().as_u16(),
                message: resp.text().await.unwrap_or_default(),
            });
        }

        let data = resp
            .json::<FileInfoApiResponse>()
            .await
            .map_err(|e| StorageError::Network(format!("Failed to parse file info JSON: {}", e)))?;

        if let (Some(uid), Some(size)) = (data.uid, data.size) {
            Ok(Some(YukisboxFileInfo {
                uid,
                size,
                filename: data.filename,
                content_type: data.content_type,
            }))
        } else {
            Ok(None)
        }
    }

    /// Verify that an object exists and has size > 0
    pub async fn verify_file(&self, uid: &str) -> Result<bool, StorageError> {
        match self.get_file_info(uid).await? {
            Some(info) if info.size > 0 => Ok(true),
            Some(info) => Err(StorageError::VerificationFailed {
                uid: uid.to_string(),
                size: info.size,
            }),
            None => Ok(false),
        }
    }

    /// Method A: Remote URL upload via POST /v1/upload-url (form-urlencoded)
    pub async fn upload_url(
        &self,
        source_url: &str,
        custom_slug: Option<&str>,
    ) -> Result<UploadResult, StorageError> {
        // Validate URL against SSRF
        validate_url_ssrf(source_url).map_err(|e| StorageError::Security(e.to_string()))?;

        let clean_slug = custom_slug.map(sanitize_slug).filter(|s| !s.is_empty());

        // Pre-upload existence check
        if let Some(ref slug) = clean_slug {
            if let Ok(Some(existing)) = self.get_file_info(slug).await {
                if existing.size > 0 {
                    info!(
                        "Yukysbox: slug '{}' already exists on storage. Reusing existing object.",
                        slug
                    );
                    return Ok(UploadResult {
                        uid: existing.uid.clone(),
                        url: format!("{}/file/{}", self.base_url, existing.uid),
                        file_size: Some(existing.size),
                        reused: true,
                    });
                }
            }
        }

        let mut target_slug = clean_slug;
        let mut attempt = 0;
        let max_collision_retries = 3;

        while attempt < max_collision_retries {
            let mut params = vec![("url", source_url.to_string())];
            if let Some(ref slug) = target_slug {
                params.push(("custom_slug", slug.clone()));
            }

            let endpoint = format!("{}/v1/upload-url", self.base_url);
            let resp = match self.client.post(&endpoint).form(&params).send().await {
                Ok(r) => r,
                Err(e) => {
                    if e.is_timeout() {
                        return Err(StorageError::Timeout(self.timeout.as_secs()));
                    }
                    return Err(StorageError::Network(e.to_string()));
                }
            };

            let status = resp.status();
            let body_text = resp.text().await.unwrap_or_default();

            if status.is_success() {
                if let Ok(res_json) = serde_json::from_str::<UploadApiResponse>(&body_text) {
                    if res_json.success.unwrap_or(false) {
                        if let Some(uid) = res_json.uid {
                            let file_url = res_json
                                .url
                                .unwrap_or_else(|| format!("{}/file/{}", self.base_url, uid));

                            // Post-upload verification
                            let file_size = match self.get_file_info(&uid).await {
                                Ok(Some(info)) if info.size > 0 => Some(info.size),
                                Ok(Some(info)) => {
                                    return Err(StorageError::VerificationFailed {
                                        uid,
                                        size: info.size,
                                    });
                                }
                                Ok(None) => None,
                                Err(e) => {
                                    warn!("Non-fatal verification warning for {}: {}", uid, e);
                                    None
                                }
                            };

                            return Ok(UploadResult {
                                uid,
                                url: file_url,
                                file_size,
                                reused: false,
                            });
                        }
                    }
                }
                return Err(StorageError::UploadFailed(format!(
                    "Invalid upload-url response: {}",
                    body_text
                )));
            }

            // Slug collision handling: "already taken"
            if status == reqwest::StatusCode::BAD_REQUEST
                && body_text.to_lowercase().contains("already taken")
            {
                attempt += 1;
                if let Some(ref base) = target_slug {
                    target_slug = Some(format!("{}-v{}", base, attempt + 1));
                    info!("Yukysbox: slug taken, retrying as {:?}", target_slug);
                    continue;
                }
            }

            return Err(StorageError::Http {
                status: status.as_u16(),
                message: body_text,
            });
        }

        Err(StorageError::UploadFailed(format!(
            "Method A failed after {} collision attempts",
            max_collision_retries
        )))
    }

    /// Method B fallback: Streaming multipart file upload via POST /v1/upload
    pub async fn upload_file(
        &self,
        file_path: &Path,
        custom_slug: Option<&str>,
        mime_type: Option<&str>,
    ) -> Result<UploadResult, StorageError> {
        if !file_path.exists() {
            return Err(StorageError::Io(format!(
                "File does not exist: {:?}",
                file_path
            )));
        }

        let metadata = fs::metadata(file_path).map_err(|e| StorageError::Io(e.to_string()))?;
        let file_size = metadata.len() as i64;

        if file_size <= 0 {
            return Err(StorageError::UploadFailed(
                "Refusing to upload 0-byte file".to_string(),
            ));
        }

        let clean_slug = custom_slug.map(sanitize_slug).filter(|s| !s.is_empty());

        // Pre-upload check
        if let Some(ref slug) = clean_slug {
            if let Ok(Some(existing)) = self.get_file_info(slug).await {
                if existing.size > 0 {
                    info!(
                        "Yukysbox: slug '{}' already exists on storage. Reusing.",
                        slug
                    );
                    return Ok(UploadResult {
                        uid: existing.uid.clone(),
                        url: format!("{}/file/{}", self.base_url, existing.uid),
                        file_size: Some(existing.size),
                        reused: true,
                    });
                }
            }
        }

        let file_content = fs::read(file_path)
            .map_err(|e| StorageError::Io(format!("Failed reading file for upload: {}", e)))?;

        let file_name = file_path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("audio.mp3")
            .to_string();

        let content_type = mime_type.unwrap_or("audio/mpeg").to_string();

        let mut target_slug = clean_slug;
        let mut attempt = 0;
        let max_collision_retries = 3;

        while attempt < max_collision_retries {
            let part = multipart::Part::bytes(file_content.clone())
                .file_name(file_name.clone())
                .mime_str(&content_type)
                .map_err(|e| StorageError::UploadFailed(e.to_string()))?;

            let mut form = multipart::Form::new().part("file", part);

            if let Some(ref slug) = target_slug {
                form = form.text("custom_slug", slug.clone());
            }

            let endpoint = format!("{}/v1/upload", self.base_url);
            let resp = match self.client.post(&endpoint).multipart(form).send().await {
                Ok(r) => r,
                Err(e) => {
                    if e.is_timeout() {
                        return Err(StorageError::Timeout(self.timeout.as_secs()));
                    }
                    return Err(StorageError::Network(e.to_string()));
                }
            };

            let status = resp.status();
            let body_text = resp.text().await.unwrap_or_default();

            if status.is_success() {
                if let Ok(res_json) = serde_json::from_str::<UploadApiResponse>(&body_text) {
                    if res_json.success.unwrap_or(false) {
                        if let Some(uid) = res_json.uid {
                            let file_url = res_json
                                .url
                                .unwrap_or_else(|| format!("{}/file/{}", self.base_url, uid));

                            // Post-upload verification
                            let verified_size = match self.get_file_info(&uid).await {
                                Ok(Some(info)) if info.size > 0 => Some(info.size),
                                Ok(Some(info)) => {
                                    return Err(StorageError::VerificationFailed {
                                        uid,
                                        size: info.size,
                                    });
                                }
                                _ => Some(file_size),
                            };

                            return Ok(UploadResult {
                                uid,
                                url: file_url,
                                file_size: verified_size,
                                reused: false,
                            });
                        }
                    }
                }
                return Err(StorageError::UploadFailed(format!(
                    "Invalid upload response: {}",
                    body_text
                )));
            }

            if status == reqwest::StatusCode::BAD_REQUEST
                && body_text.to_lowercase().contains("already taken")
            {
                attempt += 1;
                if let Some(ref base) = target_slug {
                    target_slug = Some(format!("{}-v{}", base, attempt + 1));
                    info!(
                        "Yukysbox: multipart slug taken, retrying as {:?}",
                        target_slug
                    );
                    continue;
                }
            }

            return Err(StorageError::Http {
                status: status.as_u16(),
                message: body_text,
            });
        }

        Err(StorageError::UploadFailed(
            "Method B multipart upload failed".to_string(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sanitize_slug_clean() {
        assert_eq!(sanitize_slug("Chaleya - Jawan"), "chaleya-jawan");
        assert_eq!(
            sanitize_slug("Kesariya (From \"Brahmastra\")"),
            "kesariya-from-brahmastra"
        );
        assert_eq!(
            sanitize_slug("Dil Tu Jaan Tu (feat. Stebin Ben)"),
            "dil-tu-jaan-tu-feat-stebin-ben"
        );
    }

    #[test]
    fn test_sanitize_slug_length_limit() {
        let long_title = "a".repeat(120);
        let cleaned = sanitize_slug(&long_title);
        assert_eq!(cleaned.len(), 80);
    }

    #[test]
    fn test_sanitize_slug_special_characters() {
        assert_eq!(sanitize_slug("!@#$%^&*()_+"), "");
        assert_eq!(sanitize_slug("Song #1 - Artist!"), "song-1-artist");
    }

    #[tokio::test]
    async fn test_yukysbox_initialization() {
        let storage = YukisboxStorage::new(None, 30).unwrap();
        assert_eq!(storage.base_url(), "https://yukiapi.site");
    }

    #[tokio::test]
    async fn test_ssrf_rejection_in_upload_url() {
        let storage = YukisboxStorage::new(None, 30).unwrap();
        let res = storage
            .upload_url("http://127.0.0.1:8000/internal", None)
            .await;
        assert!(res.is_err());
        match res.unwrap_err() {
            StorageError::Security(_) => {}
            other => panic!("Expected Security error, got {:?}", other),
        }
    }
}
