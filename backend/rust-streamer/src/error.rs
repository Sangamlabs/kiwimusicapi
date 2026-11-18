use thiserror::Error;

#[derive(Error, Debug, Clone, PartialEq)]
pub enum ProviderError {
    #[error("Track not found: {0}")]
    NotFound(String),

    #[error("Match confidence below threshold: query='{query}', score={score:.2}, threshold={threshold:.2}")]
    LowConfidenceMatch {
        query: String,
        score: f64,
        threshold: f64,
    },

    #[error("Network error for {provider}: {message}")]
    Network { provider: String, message: String },

    #[error("Provider timeout after {timeout_secs}s for {provider}")]
    Timeout { provider: String, timeout_secs: u64 },

    #[error("Media decryption failed: {0}")]
    Decryption(String),

    #[error("yt-dlp extraction error: {0}")]
    Extraction(String),

    #[error("Unsupported media or URL: {0}")]
    Unsupported(String),

    #[error("Security violation (SSRF/Invalid Host): {0}")]
    Security(String),

    #[error("All configured providers failed: {0:?}")]
    AllProvidersFailed(Vec<String>),
}
