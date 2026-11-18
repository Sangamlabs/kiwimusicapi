pub mod jiosaavn;
pub mod ytdlp;

use std::sync::Arc;
use tracing::{info, warn};

use crate::config::ProviderConfig;
use crate::error::ProviderError;
use crate::models::TrackMetadata;
use crate::security::SecurityValidator;
use jiosaavn::JioSaavnProvider;
use ytdlp::YtDlpProvider;

#[derive(Clone)]
pub struct ProviderManager {
    jiosaavn: Arc<JioSaavnProvider>,
    ytdlp: Arc<YtDlpProvider>,
    security: Arc<SecurityValidator>,
    provider_order: Vec<String>,
}

impl ProviderManager {
    pub fn new(config: &ProviderConfig) -> Self {
        let jiosaavn = Arc::new(JioSaavnProvider::new(
            config.jiosaavn_base_url.clone(),
            config.jiosaavn_timeout_secs,
            config.match_threshold,
        ));

        let ytdlp = Arc::new(YtDlpProvider::new(
            config.ytdlp_executable.clone(),
            config.ytdlp_timeout_secs,
            config.max_concurrency,
        ));

        let security = Arc::new(SecurityValidator::new(config.allowed_media_hosts.clone()));

        Self {
            jiosaavn,
            ytdlp,
            security,
            provider_order: config.provider_order.clone(),
        }
    }

    pub fn jiosaavn(&self) -> &Arc<JioSaavnProvider> {
        &self.jiosaavn
    }

    pub fn ytdlp(&self) -> &Arc<YtDlpProvider> {
        &self.ytdlp
    }

    pub fn security(&self) -> &Arc<SecurityValidator> {
        &self.security
    }

    /// Search across eligible providers.
    pub async fn search(
        &self,
        query: &str,
        limit: usize,
    ) -> Result<Vec<TrackMetadata>, ProviderError> {
        // Search primary provider (JioSaavn)
        self.jiosaavn.search(query, limit).await
    }

    /// Resolve track metadata and authorized audio stream URL using fallback orchestration.
    pub async fn resolve(&self, track_id: &str) -> Result<TrackMetadata, ProviderError> {
        let mut errors = Vec::new();

        for provider_name in &self.provider_order {
            match provider_name.as_str() {
                "jiosaavn" => {
                    info!(
                        "Attempting resolution with provider 'jiosaavn' for '{}'...",
                        track_id
                    );
                    match self.jiosaavn.get_track(track_id).await {
                        Ok(Some(track)) => {
                            // Validate source URL security
                            if let Err(sec_err) = self.security.validate_url(&track.source_url) {
                                warn!(
                                    "JioSaavn resolved URL rejected by security validator: {}",
                                    sec_err
                                );
                                errors.push(format!("jiosaavn security rejection: {}", sec_err));
                            } else {
                                info!(
                                    "Successfully resolved track '{}' via JioSaavn ('{}' by '{}')",
                                    track_id, track.title, track.artist
                                );
                                return Ok(track);
                            }
                        }
                        Ok(None) => {
                            info!("JioSaavn returned no confident match for '{}'. Triggering fallback.", track_id);
                            errors.push("jiosaavn: no confident match found".to_string());
                        }
                        Err(e) => {
                            warn!(
                                "JioSaavn resolution error for '{}': {}. Triggering fallback.",
                                track_id, e
                            );
                            errors.push(format!("jiosaavn: {}", e));
                        }
                    }
                }
                "ytdlp" => {
                    info!(
                        "Attempting fallback resolution with provider 'ytdlp' for '{}'...",
                        track_id
                    );
                    match self.ytdlp.extract_info(track_id).await {
                        Ok(Some(track)) => {
                            // Validate source URL security
                            if let Err(sec_err) = self.security.validate_url(&track.source_url) {
                                warn!(
                                    "yt-dlp resolved URL rejected by security validator: {}",
                                    sec_err
                                );
                                errors.push(format!("ytdlp security rejection: {}", sec_err));
                            } else {
                                info!(
                                    "Successfully resolved track '{}' via yt-dlp ('{}' by '{}')",
                                    track_id, track.title, track.artist
                                );
                                return Ok(track);
                            }
                        }
                        Ok(None) => {
                            info!("yt-dlp returned no result for '{}'.", track_id);
                            errors.push("ytdlp: media unavailable or private".to_string());
                        }
                        Err(e) => {
                            warn!("yt-dlp extraction error for '{}': {}", track_id, e);
                            errors.push(format!("ytdlp: {}", e));
                        }
                    }
                }
                other => {
                    warn!("Unknown provider '{}' in provider_order. Skipping.", other);
                }
            }
        }

        Err(ProviderError::AllProvidersFailed(errors))
    }
}
