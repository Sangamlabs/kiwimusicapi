use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct TrackMetadata {
    pub provider: String,
    pub provider_song_id: String,
    pub track_id: String,
    pub title: String,
    pub artist: String,
    pub album: Option<String>,
    pub duration: u64,
    pub language: Option<String>,
    pub thumbnail: Option<String>,
    pub source_url: String,
    pub audio_format: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResolveRequest {
    pub track_id: String,
    pub query: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResolveResponse {
    pub success: bool,
    pub track: Option<TrackMetadata>,
    pub error: Option<String>,
}
