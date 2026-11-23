use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::time::Duration;
use thiserror::Error;
use tracing::{error, info, warn};

use crate::db::{DbTrackRecord, LeaseResult, PostgresManager};
use crate::error::ProviderError;
use crate::models::TrackMetadata;
use crate::providers::ProviderManager;
use crate::security::validate_url_ssrf;
use crate::storage::{sanitize_slug, StorageError, YukisboxStorage};

#[derive(Error, Debug)]
pub enum AcquisitionError {
    #[error("Provider resolution error: {0}")]
    Provider(#[from] ProviderError),

    #[error("Storage error: {0}")]
    Storage(#[from] StorageError),

    #[error("Database error: {0}")]
    Database(String),

    #[error("Security violation (SSRF): {0}")]
    Security(String),

    #[error("Lock acquisition timeout: {0}")]
    LockTimeout(String),

    #[error("Acquisition failed: {0}")]
    General(String),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AcquiredTrack {
    pub track_id: String,
    pub provider: String,
    pub provider_song_id: String,
    pub title: String,
    pub artist: String,
    pub album: Option<String>,
    pub duration: u64,
    pub thumbnail: Option<String>,
    pub storage_provider: String,
    pub storage_uid: String,
    pub storage_url: String,
    pub file_size: Option<i64>,
    pub audio_format: String,
    pub cached: bool,
}

pub struct AcquisitionCoordinator {
    provider_manager: ProviderManager,
    storage: YukisboxStorage,
    db: Option<PostgresManager>,
    temp_dir: PathBuf,
}

impl AcquisitionCoordinator {
    pub fn new(
        provider_manager: ProviderManager,
        storage: YukisboxStorage,
        db: Option<PostgresManager>,
        temp_dir: Option<PathBuf>,
    ) -> Self {
        let temp = temp_dir.unwrap_or_else(|| std::env::temp_dir().join("kiwi_cache"));
        let _ = std::fs::create_dir_all(&temp);

        Self {
            provider_manager,
            storage,
            db,
            temp_dir: temp,
        }
    }

    /// High-speed check if track already exists in PostgreSQL verified storage cache (< 2ms)
    pub async fn find_cached(&self, track_id: &str) -> Option<AcquiredTrack> {
        let clean_track_id = track_id.trim();
        if let Some(ref db) = self.db {
            if let Ok(Some(cached_track)) = db.find_track(clean_track_id).await {
                if let Some(storage_url) = cached_track.storage_url {
                    return Some(AcquiredTrack {
                        track_id: cached_track.track_id,
                        provider: cached_track.provider,
                        provider_song_id: cached_track.provider_song_id,
                        title: cached_track.title,
                        artist: cached_track.artist,
                        album: cached_track.album,
                        duration: cached_track.duration as u64,
                        thumbnail: cached_track.thumbnail,
                        storage_provider: cached_track.storage_provider,
                        storage_uid: cached_track.storage_uid.unwrap_or_default(),
                        storage_url,
                        file_size: cached_track.file_size,
                        audio_format: cached_track.audio_format,
                        cached: true,
                    });
                }
            }
        }
        None
    }

    /// Primary execution flow: Cache-first lookup -> Distributed single-flight lock -> Provider resolution -> Yukysbox upload -> Storage verification -> DB persistence
    pub async fn acquire(
        &self,
        track_id: &str,
        _query: Option<&str>,
    ) -> Result<AcquiredTrack, AcquisitionError> {
        let clean_track_id = track_id.trim();

        // 1. Check PostgreSQL for verified cached track (Step 3 in architecture)
        if let Some(cached) = self.find_cached(clean_track_id).await {
            info!(
                "Cache HIT for track_id: {} (returning existing reference)",
                clean_track_id
            );
            return Ok(cached);
        }

        if let Some(ref db) = self.db {
            // 2. Obtain distributed single-flight lease on upload_jobs (Step 6)
            let mut attempts = 0;
            let max_wait_attempts = 60; // 30 seconds total (60 * 500ms)
            let mut held_lease = false;

            while attempts < max_wait_attempts {
                match db.try_acquire_lease(clean_track_id, 60).await {
                    Ok(LeaseResult::Acquired) => {
                        held_lease = true;
                        break;
                    }
                    Ok(LeaseResult::AlreadyCompleted) => {
                        // Winner completed! Re-fetch and return
                        if let Ok(Some(ready)) = db.find_track(clean_track_id).await {
                            if let Some(url) = ready.storage_url {
                                return Ok(AcquiredTrack {
                                    track_id: ready.track_id,
                                    provider: ready.provider,
                                    provider_song_id: ready.provider_song_id,
                                    title: ready.title,
                                    artist: ready.artist,
                                    album: ready.album,
                                    duration: ready.duration as u64,
                                    thumbnail: ready.thumbnail,
                                    storage_provider: ready.storage_provider,
                                    storage_uid: ready.storage_uid.unwrap_or_default(),
                                    storage_url: url,
                                    file_size: ready.file_size,
                                    audio_format: ready.audio_format,
                                    cached: true,
                                });
                            }
                        }
                        tokio::time::sleep(Duration::from_millis(500)).await;
                    }
                    Ok(LeaseResult::LockedByOther { .. }) => {
                        // Another worker is currently acquiring. Wait and poll
                        tokio::time::sleep(Duration::from_millis(500)).await;
                        if let Ok(Some(ready)) = db.find_track(clean_track_id).await {
                            if let Some(url) = ready.storage_url {
                                return Ok(AcquiredTrack {
                                    track_id: ready.track_id,
                                    provider: ready.provider,
                                    provider_song_id: ready.provider_song_id,
                                    title: ready.title,
                                    artist: ready.artist,
                                    album: ready.album,
                                    duration: ready.duration as u64,
                                    thumbnail: ready.thumbnail,
                                    storage_provider: ready.storage_provider,
                                    storage_uid: ready.storage_uid.unwrap_or_default(),
                                    storage_url: url,
                                    file_size: ready.file_size,
                                    audio_format: ready.audio_format,
                                    cached: true,
                                });
                            }
                        }
                    }
                    Err(e) => {
                        warn!("Database lease check warning: {}", e);
                        break;
                    }
                }
                attempts += 1;
            }

            if !held_lease {
                // If we didn't get the lease and timeout expired, check DB one last time
                if let Ok(Some(ready)) = db.find_track(clean_track_id).await {
                    if let Some(url) = ready.storage_url {
                        return Ok(AcquiredTrack {
                            track_id: ready.track_id,
                            provider: ready.provider,
                            provider_song_id: ready.provider_song_id,
                            title: ready.title,
                            artist: ready.artist,
                            album: ready.album,
                            duration: ready.duration as u64,
                            thumbnail: ready.thumbnail,
                            storage_provider: ready.storage_provider,
                            storage_uid: ready.storage_uid.unwrap_or_default(),
                            storage_url: url,
                            file_size: ready.file_size,
                            audio_format: ready.audio_format,
                            cached: true,
                        });
                    }
                }
            }

            // 3. Double-check cache after lease acquisition (Step 7 & 8)
            if let Ok(Some(ready)) = db.find_track(clean_track_id).await {
                if let Some(url) = ready.storage_url {
                    let _ = db.mark_job_completed(clean_track_id).await;
                    return Ok(AcquiredTrack {
                        track_id: ready.track_id,
                        provider: ready.provider,
                        provider_song_id: ready.provider_song_id,
                        title: ready.title,
                        artist: ready.artist,
                        album: ready.album,
                        duration: ready.duration as u64,
                        thumbnail: ready.thumbnail,
                        storage_provider: ready.storage_provider,
                        storage_uid: ready.storage_uid.unwrap_or_default(),
                        storage_url: url,
                        file_size: ready.file_size,
                        audio_format: ready.audio_format,
                        cached: true,
                    });
                }
            }
        }

        // 4. Invoke provider resolution engine (Step 9)
        let resolved_meta = self.provider_manager.resolve(clean_track_id).await;

        let track_meta = match resolved_meta {
            Ok(meta) => meta,
            Err(e) => {
                if let Some(ref db) = self.db {
                    let _ = db.mark_job_failed(clean_track_id, &e.to_string()).await;
                }
                return Err(AcquisitionError::Provider(e));
            }
        };

        // 5. Acquire and validate permitted media (Step 10)
        let source_url = track_meta.source_url.clone();
        if let Err(e) = validate_url_ssrf(&source_url) {
            if let Some(ref db) = self.db {
                let _ = db.mark_job_failed(clean_track_id, &e.to_string()).await;
            }
            return Err(AcquisitionError::Security(e.to_string()));
        }

        // 6. Direct Provider Audio Link Resolution (Yukisbox completely removed)
        let storage_uid = track_meta.provider_song_id.clone();
        let storage_url = source_url.clone();

        // 7. Persist direct stream cache record to PostgreSQL (Step 13 & 14)
        if let Some(ref db) = self.db {
            let db_record = DbTrackRecord {
                track_id: clean_track_id.to_string(),
                provider: track_meta.provider.clone(),
                provider_song_id: track_meta.provider_song_id.clone(),
                title: track_meta.title.clone(),
                artist: track_meta.artist.clone(),
                album: track_meta.album.clone(),
                duration: track_meta.duration as i32,
                language: track_meta.language.clone(),
                thumbnail: track_meta.thumbnail.clone(),
                source_url: Some(source_url.clone()),
                storage_provider: "jiosaavn_direct".to_string(),
                storage_uid: Some(storage_uid.clone()),
                storage_url: Some(storage_url.clone()),
                file_size: None,
                audio_format: track_meta.audio_format.clone(),
                status: "available".to_string(),
            };

            if let Err(e) = db.upsert_track(&db_record).await {
                error!("Failed to persist track to PostgreSQL: {}", e);
            }
            let _ = db.mark_job_completed(clean_track_id).await;
        }

        // 8. Return direct streaming media reference
        Ok(AcquiredTrack {
            track_id: clean_track_id.to_string(),
            provider: track_meta.provider,
            provider_song_id: track_meta.provider_song_id,
            title: track_meta.title,
            artist: track_meta.artist,
            album: track_meta.album,
            duration: track_meta.duration,
            thumbnail: track_meta.thumbnail,
            storage_provider: "jiosaavn_direct".to_string(),
            storage_uid,
            storage_url,
            file_size: None,
            audio_format: track_meta.audio_format,
            cached: false,
        })
    }

    /// Helper for Method B fallback: Downloads media from source_url and uploads multipart to Yukysbox
    async fn execute_method_b_fallback(
        &self,
        source_url: &str,
        slug: &str,
        track_meta: &TrackMetadata,
    ) -> Result<crate::storage::UploadResult, StorageError> {
        let temp_filename = format!(
            "temp_{}_{}.{}",
            slug,
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_millis())
                .unwrap_or(0),
            track_meta.audio_format
        );
        let temp_file_path = self.temp_dir.join(&temp_filename);

        // Stream audio bytes to temporary file
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(60))
            .build()
            .map_err(|e| StorageError::Network(e.to_string()))?;

        let mut resp = client
            .get(source_url)
            .send()
            .await
            .map_err(|e| StorageError::Network(e.to_string()))?;

        if !resp.status().is_success() {
            return Err(StorageError::Http {
                status: resp.status().as_u16(),
                message: format!("Source stream download failed: {}", resp.status()),
            });
        }

        use std::io::Write;
        let mut file = std::fs::File::create(&temp_file_path)
            .map_err(|e| StorageError::Io(format!("Failed to create temp file: {}", e)))?;

        while let Some(chunk) = resp
            .chunk()
            .await
            .map_err(|e| StorageError::Network(e.to_string()))?
        {
            file.write_all(&chunk)
                .map_err(|e| StorageError::Io(format!("Failed writing chunk: {}", e)))?;
        }
        drop(file);

        let upload_res = self
            .storage
            .upload_file(&temp_file_path, Some(slug), Some("audio/mpeg"))
            .await;

        // Clean up temporary file
        let _ = std::fs::remove_file(&temp_file_path);

        upload_res
    }
}
