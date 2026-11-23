use axum::{
    body::Body,
    extract::{Path, State},
    http::{header, HeaderMap, HeaderValue, StatusCode},
    response::{IntoResponse, Json, Response},
};
use futures_util::TryStreamExt;
use serde_json::json;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{broadcast, Mutex, RwLock};
use tracing::{error, info, warn};

use crate::acquisition::AcquisitionCoordinator;
use crate::providers::ProviderManager;
use crate::security::validate_url_ssrf;
use crate::storage::YukisboxStorage;

#[derive(Clone, Debug)]
pub struct CachedStreamUrl {
    pub url: String,
    pub audio_format: String,
    pub created_at: std::time::Instant,
}

#[derive(Clone)]
pub struct AppState {
    pub provider_manager: Arc<ProviderManager>,
    pub storage: Arc<YukisboxStorage>,
    pub coordinator: Arc<AcquisitionCoordinator>,
    pub http_client: reqwest::Client,
    pub stream_cache: Arc<RwLock<HashMap<String, CachedStreamUrl>>>,
    pub flight_group: Arc<Mutex<HashMap<String, broadcast::Sender<Result<CachedStreamUrl, String>>>>>,
}

impl AppState {
    pub fn new(
        provider_manager: Arc<ProviderManager>,
        storage: Arc<YukisboxStorage>,
        coordinator: Arc<AcquisitionCoordinator>,
    ) -> Self {
        let http_client = reqwest::Client::builder()
            .timeout(Duration::from_secs(60))
            .connect_timeout(Duration::from_secs(5))
            .pool_idle_timeout(Duration::from_secs(120))
            .pool_max_idle_per_host(100)
            .tcp_nodelay(true)
            .build()
            .unwrap_or_else(|_| reqwest::Client::new());

        Self {
            provider_manager,
            storage,
            coordinator,
            http_client,
            stream_cache: Arc::new(RwLock::new(HashMap::new())),
            flight_group: Arc::new(Mutex::new(HashMap::new())),
        }
    }
}

pub async fn health_handler() -> impl IntoResponse {
    Json(json!({
        "status": "healthy",
        "service": "kiwi-rust-streamer",
        "version": "0.1.0"
    }))
}

/// Single-Flight Track Stream Target Resolver:
/// Resolves the canonical direct audio URL across PostgreSQL storage cache, JioSaavn, yt-dlp, and Yukysbox.
async fn resolve_stream_target(
    state: &AppState,
    clean_track_id: &str,
) -> Result<CachedStreamUrl, String> {
    // 1. High-speed PostgreSQL verified storage cache check (< 2ms)
    if let Some(cached) = state.coordinator.find_cached(clean_track_id).await {
        if let Ok(safe_url) = validate_url_ssrf(&cached.storage_url) {
            info!(
                "Stream: Resolved from PostgreSQL storage cache for '{}'",
                clean_track_id
            );
            return Ok(CachedStreamUrl {
                url: safe_url.as_str().to_string(),
                audio_format: cached.audio_format,
                created_at: std::time::Instant::now(),
            });
        }
    }

    // 2. Primary Direct Provider: JioSaavn
    info!(
        "Stream: Attempting primary direct stream via JioSaavn for '{}'...",
        clean_track_id
    );
    match state
        .provider_manager
        .jiosaavn()
        .get_track(clean_track_id)
        .await
    {
        Ok(Some(meta)) => {
            if let Ok(safe_url) = validate_url_ssrf(&meta.source_url) {
                info!(
                    "Stream: Resolved direct JioSaavn URL for '{}'",
                    clean_track_id
                );
                return Ok(CachedStreamUrl {
                    url: safe_url.as_str().to_string(),
                    audio_format: meta.audio_format,
                    created_at: std::time::Instant::now(),
                });
            } else {
                warn!(
                    "Stream: JioSaavn source URL rejected by SSRF guard for '{}'",
                    clean_track_id
                );
            }
        }
        Ok(None) => {
            info!(
                "Stream: Track '{}' not found on JioSaavn. Falling back to yt-dlp.",
                clean_track_id
            );
        }
        Err(e) => {
            warn!(
                "Stream: JioSaavn resolution error for '{}': {}. Falling back to yt-dlp.",
                clean_track_id, e
            );
        }
    }

    // 3. Secondary Direct Provider: Managed yt-dlp
    info!(
        "Stream: Attempting secondary direct stream via yt-dlp for '{}'...",
        clean_track_id
    );
    match state
        .provider_manager
        .ytdlp()
        .extract_info(clean_track_id)
        .await
    {
        Ok(Some(meta)) => {
            if let Ok(safe_url) = validate_url_ssrf(&meta.source_url) {
                info!(
                    "Stream: Resolved direct yt-dlp URL for '{}'",
                    clean_track_id
                );
                return Ok(CachedStreamUrl {
                    url: safe_url.as_str().to_string(),
                    audio_format: meta.audio_format,
                    created_at: std::time::Instant::now(),
                });
            } else {
                warn!(
                    "Stream: yt-dlp source URL rejected by SSRF guard for '{}'",
                    clean_track_id
                );
            }
        }
        Ok(None) => {
            info!(
                "Stream: Track '{}' not resolved by yt-dlp. Falling back to Yukysbox.",
                clean_track_id
            );
        }
        Err(e) => {
            warn!(
                "Stream: yt-dlp resolution error for '{}': {}. Falling back to acquisition coordinator.",
                clean_track_id, e
            );
        }
    }

    // 4. Fallback Workflow: Acquisition Coordinator (Direct provider stream resolution)
    info!(
        "Stream: All direct providers unindexed. Invoking acquisition coordinator for '{}'...",
        clean_track_id
    );
    match state.coordinator.acquire(clean_track_id, None).await {
        Ok(acquired) => {
            if let Ok(safe_url) = validate_url_ssrf(&acquired.storage_url) {
                info!(
                    "Stream: Resolved direct stream URL for '{}'",
                    clean_track_id
                );
                return Ok(CachedStreamUrl {
                    url: safe_url.as_str().to_string(),
                    audio_format: acquired.audio_format,
                    created_at: std::time::Instant::now(),
                });
            }
        }
        Err(e) => {
            error!(
                "Stream: Direct acquisition fallback failed for '{}': {}",
                clean_track_id, e
            );
        }
    }

    Err(format!(
        "Unable to stream track '{}' across all eligible providers and storage",
        clean_track_id
    ))
}

/// Primary Playback Handler:
/// Direct Provider Streaming with HTTP Range support, multi-user single-flight request grabber, and Yukysbox fallback.
pub async fn stream_handler(
    State(state): State<AppState>,
    Path(track_id): Path<String>,
    headers: HeaderMap,
) -> Response {
    let clean_track_id = track_id.trim();
    let range_header = headers.get(header::RANGE).cloned();

    // 0. High-speed In-Memory Stream Cache Check (< 1ms TTFB for subsequent Range requests / seeking)
    {
        let cache_read = state.stream_cache.read().await;
        if let Some(entry) = cache_read.get(clean_track_id) {
            if entry.created_at.elapsed() < Duration::from_secs(7200) {
                if let Ok(safe_url) = validate_url_ssrf(&entry.url) {
                    match proxy_upstream_stream(
                        &state.http_client,
                        safe_url.as_str(),
                        range_header.as_ref(),
                        &entry.audio_format,
                    )
                    .await
                    {
                        Ok(resp) => {
                            info!(
                                "Stream: In-memory cache HIT for '{}' (sub-millisecond)",
                                clean_track_id
                            );
                            return resp;
                        }
                        Err(e) => {
                            warn!(
                                "Stream: Cached in-memory URL stream failed ({}). Evicting dead entry.",
                                e
                            );
                            drop(cache_read);
                            state.stream_cache.write().await.remove(clean_track_id);
                        }
                    }
                }
            }
        }
    }

    // 1. Single-Flight Request Grabber & Coalescer:
    // If multiple concurrent users request this track at the exact same moment,
    // only ONE upstream resolution task executes. All others subscribe and receive the result simultaneously.
    let resolved_result = {
        let mut inflight = state.flight_group.lock().await;
        if let Some(tx) = inflight.get(clean_track_id) {
            let mut rx = tx.subscribe();
            drop(inflight);
            info!(
                "Stream: Multiple users requesting '{}' - subscribed to in-flight grabber",
                clean_track_id
            );
            match rx.recv().await {
                Ok(res) => res,
                Err(_) => resolve_stream_target(&state, clean_track_id).await,
            }
        } else {
            let (tx, _rx) = broadcast::channel(64);
            inflight.insert(clean_track_id.to_string(), tx.clone());
            drop(inflight);

            let res = resolve_stream_target(&state, clean_track_id).await;

            // Cache successfully resolved stream target
            if let Ok(ref entry) = res {
                let mut cache_write = state.stream_cache.write().await;
                cache_write.insert(clean_track_id.to_string(), entry.clone());
            }

            // Broadcast resolved target to all queued concurrent user requests
            let _ = tx.send(res.clone());

            // Remove from flight group
            let mut inflight = state.flight_group.lock().await;
            inflight.remove(clean_track_id);

            res
        }
    };

    // 2. Stream audio chunks to client with Range and backpressure support
    match resolved_result {
        Ok(entry) => {
            match proxy_upstream_stream(
                &state.http_client,
                &entry.url,
                range_header.as_ref(),
                &entry.audio_format,
            )
            .await
            {
                Ok(resp) => resp,
                Err(e) => {
                    error!(
                        "Stream: Upstream proxy failed for '{}': {}",
                        clean_track_id, e
                    );
                    (
                        StatusCode::BAD_GATEWAY,
                        Json(json!({
                            "success": false,
                            "error": {
                                "code": "UPSTREAM_STREAM_ERROR",
                                "message": format!("Upstream stream failed: {}", e)
                            }
                        })),
                    )
                        .into_response()
                }
            }
        }
        Err(err_msg) => (
            StatusCode::NOT_FOUND,
            Json(json!({
                "success": false,
                "error": {
                    "code": "TRACK_NOT_FOUND",
                    "message": err_msg
                }
            })),
        )
            .into_response(),
    }
}

/// Internal Acquire Handler for Python FastAPI
pub async fn internal_acquire_handler(
    State(state): State<AppState>,
    Json(payload): Json<serde_json::Value>,
) -> Response {
    let track_id = payload
        .get("track_id")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let query = payload.get("query").and_then(|v| v.as_str());

    if track_id.trim().is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({
                "success": false,
                "error": "Missing track_id"
            })),
        )
            .into_response();
    }

    match state.coordinator.acquire(track_id, query).await {
        Ok(acquired) => (
            StatusCode::OK,
            Json(json!({ "success": true, "track": acquired })),
        )
            .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "success": false, "error": e.to_string() })),
        )
            .into_response(),
    }
}

/// Helper: Proxies an upstream audio URL with Range headers, backpressure, and client disconnect cancellation.
async fn proxy_upstream_stream(
    client: &reqwest::Client,
    upstream_url: &str,
    range_header: Option<&HeaderValue>,
    audio_format: &str,
) -> Result<Response, String> {
    let mut req = client
        .get(upstream_url)
        .header(
            reqwest::header::USER_AGENT,
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        .header(reqwest::header::ACCEPT, "*/*")
        .header(reqwest::header::ACCEPT_ENCODING, "identity");

    // Propagate Range header if provided by client
    if let Some(range) = range_header {
        req = req.header(reqwest::header::RANGE, range.clone());
    }

    // Send upstream request (aborts early on timeout before reading first byte)
    let upstream_resp = req
        .send()
        .await
        .map_err(|e| format!("Upstream connection error: {}", e))?;

    let upstream_status = upstream_resp.status();

    // Verify upstream returned a streamable status (200 OK or 206 Partial Content or 416 Range Unsatisfiable)
    if !upstream_status.is_success()
        && upstream_status != reqwest::StatusCode::PARTIAL_CONTENT
        && upstream_status != reqwest::StatusCode::RANGE_NOT_SATISFIABLE
    {
        return Err(format!(
            "Upstream returned non-streamable status: {}",
            upstream_status
        ));
    }

    let status_code = StatusCode::from_u16(upstream_status.as_u16()).unwrap_or(StatusCode::OK);

    // Extract and preserve upstream headers
    let content_type = upstream_resp
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string())
        .unwrap_or_else(|| match audio_format {
            "m4a" | "mp4" => "audio/mp4".to_string(),
            _ => "audio/mpeg".to_string(),
        });

    let content_length = upstream_resp
        .headers()
        .get(reqwest::header::CONTENT_LENGTH)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());

    let content_range = upstream_resp
        .headers()
        .get(reqwest::header::CONTENT_RANGE)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());

    // Stream chunks directly from upstream to downstream with backpressure and cancellation
    let byte_stream = upstream_resp.bytes_stream().map_err(std::io::Error::other);

    let body = Body::from_stream(byte_stream);

    let mut response_builder = Response::builder()
        .status(status_code)
        .header(header::CONTENT_TYPE, content_type)
        .header(header::ACCEPT_RANGES, "bytes")
        .header(header::CACHE_CONTROL, "public, max-age=86400");

    if let Some(cl) = content_length {
        response_builder = response_builder.header(header::CONTENT_LENGTH, cl);
    }

    if let Some(cr) = content_range {
        response_builder = response_builder.header(header::CONTENT_RANGE, cr);
    }

    response_builder
        .body(body)
        .map_err(|e| format!("Failed to build streaming response: {}", e))
}
