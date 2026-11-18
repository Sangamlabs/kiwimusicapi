use serde_json::Value;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;
use tokio::process::Command;
use tokio::sync::Semaphore;
use tokio::time::timeout;
use tracing::debug;

use crate::error::ProviderError;
use crate::models::TrackMetadata;

pub struct YtDlpProvider {
    executable: String,
    timeout: Duration,
    semaphore: Arc<Semaphore>,
}

impl YtDlpProvider {
    pub fn new(executable: String, timeout_secs: u64, max_concurrency: usize) -> Self {
        Self {
            executable,
            timeout: Duration::from_secs(timeout_secs),
            semaphore: Arc::new(Semaphore::new(max_concurrency.max(1))),
        }
    }

    /// Check if yt-dlp is available and return its version string.
    pub async fn check_version(&self) -> Result<String, ProviderError> {
        let mut cmd = Command::new(&self.executable);
        cmd.arg("--version")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);

        let output = match timeout(Duration::from_secs(5), cmd.output()).await {
            Ok(Ok(out)) => out,
            Ok(Err(e)) => {
                return Err(ProviderError::Extraction(format!(
                    "Failed to execute yt-dlp binary '{}': {}",
                    self.executable, e
                )))
            }
            Err(_) => {
                return Err(ProviderError::Timeout {
                    provider: "ytdlp".to_string(),
                    timeout_secs: 5,
                })
            }
        };

        if output.status.success() {
            let ver = String::from_utf8_lossy(&output.stdout).trim().to_string();
            Ok(ver)
        } else {
            let err = String::from_utf8_lossy(&output.stderr).trim().to_string();
            Err(ProviderError::Extraction(format!(
                "yt-dlp version check failed: {}",
                err
            )))
        }
    }

    /// Construct a sanitized target URL from track_id or query string.
    fn sanitize_target(target: &str) -> Result<String, ProviderError> {
        let trimmed = target.trim();
        if trimmed.is_empty() {
            return Err(ProviderError::Unsupported(
                "Empty query or target".to_string(),
            ));
        }

        // If it starts with flag prefixes, reject immediately to prevent argument injection
        if trimmed.starts_with('-') {
            return Err(ProviderError::Security(
                "Target cannot start with dash flags".to_string(),
            ));
        }

        // If it is an 11-char YouTube ID
        if trimmed.len() == 11
            && trimmed
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
        {
            return Ok(format!("https://www.youtube.com/watch?v={}", trimmed));
        }

        // If it is a full URL, validate scheme
        if trimmed.starts_with("http://") || trimmed.starts_with("https://") {
            return Ok(trimmed.to_string());
        }

        // Otherwise use ytsearch1 prefix
        Ok(format!("ytsearch1:{}", trimmed))
    }

    /// Extract media metadata and direct streaming URL using yt-dlp JSON dump.
    pub async fn extract_info(&self, target: &str) -> Result<Option<TrackMetadata>, ProviderError> {
        let target_url = Self::sanitize_target(target)?;

        // Acquire bounded concurrency permit
        let _permit =
            self.semaphore.acquire().await.map_err(|e| {
                ProviderError::Extraction(format!("Concurrency permit error: {}", e))
            })?;

        // Strict argument array without shell expansion
        let args = [
            "--dump-single-json",
            "--no-warnings",
            "--no-playlist",
            "--no-check-certificates",
            "--prefer-free-formats",
            "-f",
            "bestaudio[ext=m4a]/bestaudio/best",
            &target_url,
        ];

        let mut cmd = Command::new(&self.executable);
        cmd.args(args)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);

        debug!("Invoking yt-dlp for target: {}", target_url);

        let output = match timeout(self.timeout, cmd.output()).await {
            Ok(Ok(out)) => out,
            Ok(Err(e)) => {
                return Err(ProviderError::Extraction(format!(
                    "yt-dlp process execution failed: {}",
                    e
                )))
            }
            Err(_) => {
                return Err(ProviderError::Timeout {
                    provider: "ytdlp".to_string(),
                    timeout_secs: self.timeout.as_secs(),
                })
            }
        };

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr).to_string();
            if stderr.contains("Video unavailable")
                || stderr.contains("Private video")
                || stderr.contains("This video has been removed")
            {
                return Ok(None);
            }
            return Err(ProviderError::Extraction(format!(
                "yt-dlp failed: {}",
                stderr.trim()
            )));
        }

        let stdout_str = String::from_utf8_lossy(&output.stdout);
        let data: Value = serde_json::from_str(&stdout_str)
            .map_err(|e| ProviderError::Extraction(format!("Invalid JSON from yt-dlp: {}", e)))?;

        // In ytsearch mode, data may contain "entries"
        let item = if let Some(entries) = data.get("entries").and_then(|e| e.as_array()) {
            entries.first().unwrap_or(&data)
        } else {
            &data
        };

        let id = item
            .get("id")
            .and_then(|v| v.as_str())
            .unwrap_or(target)
            .to_string();

        let title = item
            .get("title")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown Title")
            .to_string();

        let artist = item
            .get("uploader")
            .or_else(|| item.get("creator"))
            .or_else(|| item.get("channel"))
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown Artist")
            .to_string();

        let album = item
            .get("album")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());

        let duration = item.get("duration").and_then(|v| v.as_u64()).unwrap_or(0);

        let thumbnail = item
            .get("thumbnail")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());

        let source_url = item
            .get("url")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        if source_url.is_empty() {
            return Err(ProviderError::Extraction(
                "yt-dlp did not return a valid audio source URL".to_string(),
            ));
        }

        let audio_format = item
            .get("ext")
            .and_then(|v| v.as_str())
            .unwrap_or("m4a")
            .to_string();

        Ok(Some(TrackMetadata {
            provider: "ytdlp".to_string(),
            provider_song_id: id.clone(),
            track_id: format!("kiwi_ytdlp_{}", id),
            title,
            artist,
            album,
            duration,
            language: None,
            thumbnail,
            source_url,
            audio_format,
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sanitize_target_rejects_dash_flags() {
        let res = YtDlpProvider::sanitize_target("--exec rm");
        assert!(matches!(res, Err(ProviderError::Security(_))));
    }

    #[test]
    fn test_sanitize_target_accepts_youtube_id() {
        let res = YtDlpProvider::sanitize_target("mbSp4gsKaU0").unwrap();
        assert_eq!(res, "https://www.youtube.com/watch?v=mbSp4gsKaU0");
    }

    #[test]
    fn test_sanitize_target_formats_search_query() {
        let res = YtDlpProvider::sanitize_target("Believer Imagine Dragons").unwrap();
        assert_eq!(res, "ytsearch1:Believer Imagine Dragons");
    }
}
