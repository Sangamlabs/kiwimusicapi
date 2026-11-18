pub mod acquisition;
pub mod config;
pub mod db;
pub mod error;
pub mod models;
pub mod providers;
pub mod security;
pub mod server;
pub mod storage;

use acquisition::AcquisitionCoordinator;
use config::ProviderConfig;
use db::PostgresManager;
use providers::ProviderManager;
use server::{start_server, AppState};
use std::sync::Arc;
use storage::YukisboxStorage;
use tracing::{error, info, Level};
use tracing_subscriber::FmtSubscriber;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let subscriber = FmtSubscriber::builder()
        .with_max_level(Level::INFO)
        .finish();
    let _ = tracing::subscriber::set_global_default(subscriber);

    let config = ProviderConfig::from_env();
    let manager = Arc::new(ProviderManager::new(&config));

    let yukysbox_url = std::env::var("YUKISBOX_BASE_URL").ok();
    let yukysbox_timeout = std::env::var("YUKISBOX_TIMEOUT")
        .ok()
        .and_then(|t| t.parse::<u64>().ok())
        .unwrap_or(60);
    let storage = Arc::new(YukisboxStorage::new(yukysbox_url, yukysbox_timeout)?);

    let db_manager = std::env::var("DATABASE_URL")
        .ok()
        .filter(|u| !u.trim().is_empty())
        .and_then(|u| PostgresManager::new(&u).ok());

    let coordinator = Arc::new(AcquisitionCoordinator::new(
        (*manager).clone(),
        (*storage).clone(),
        db_manager,
        None,
    ));

    let state = AppState::new(manager.clone(), storage.clone(), coordinator.clone());

    let args: Vec<String> = std::env::args().collect();

    if args.len() > 1 {
        match args[1].as_str() {
            "acquire" => {
                if args.len() < 3 {
                    eprintln!("Usage: rust-streamer acquire <track_id> [--query <query>]");
                    std::process::exit(1);
                }
                let track_id = &args[2];
                let mut query = None;
                if args.len() >= 5 && args[3] == "--query" {
                    query = Some(args[4].as_str());
                }

                match coordinator.acquire(track_id, query).await {
                    Ok(acquired) => {
                        println!("{}", serde_json::to_string(&acquired)?);
                        return Ok(());
                    }
                    Err(e) => {
                        eprintln!("{}", serde_json::json!({ "error": e.to_string() }));
                        std::process::exit(1);
                    }
                }
            }
            "resolve" => {
                if args.len() < 3 {
                    eprintln!("Usage: rust-streamer resolve <track_id>");
                    std::process::exit(1);
                }
                let track_id = &args[2];
                match manager.resolve(track_id).await {
                    Ok(track) => {
                        println!("{}", serde_json::to_string(&track)?);
                        return Ok(());
                    }
                    Err(e) => {
                        eprintln!("{}", serde_json::json!({ "error": e.to_string() }));
                        std::process::exit(1);
                    }
                }
            }
            "serve" => {
                let port = if args.len() >= 4 && args[2] == "--port" {
                    args[3].parse::<u16>().unwrap_or(8088)
                } else {
                    std::env::var("RUST_MEDIA_PORT")
                        .ok()
                        .and_then(|p| p.parse::<u16>().ok())
                        .unwrap_or(8088)
                };
                return start_server(state, port).await;
            }
            _ => {}
        }
    }

    info!("Starting Kiwi Music API — Rust Media Engine (Stop Point 5)...");
    info!(
        "Loaded Provider Configuration: order={:?}, match_threshold={:.2}",
        config.provider_order, config.match_threshold
    );

    // Verify yt-dlp binary availability
    let ytdlp = providers::ytdlp::YtDlpProvider::new(
        config.ytdlp_executable.clone(),
        config.ytdlp_timeout_secs,
        config.max_concurrency,
    );
    match ytdlp.check_version().await {
        Ok(v) => info!("Managed yt-dlp verified: version {}", v),
        Err(e) => error!("yt-dlp verification warning: {}", e),
    }

    let default_port = std::env::var("RUST_MEDIA_PORT")
        .ok()
        .and_then(|p| p.parse::<u16>().ok())
        .unwrap_or(8088);

    start_server(state, default_port).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{
        body::Body,
        http::{Request, StatusCode},
    };
    use server::build_router;
    use tower::ServiceExt;

    #[tokio::test]
    async fn test_provider_manager_initialization() {
        let config = ProviderConfig::default();
        let _manager = ProviderManager::new(&config);
        assert_eq!(config.provider_order, vec!["jiosaavn", "ytdlp"]);
    }

    #[tokio::test]
    async fn test_provider_fallback_when_both_fail() {
        let config = ProviderConfig {
            ytdlp_executable: "non_existent_ytdlp_bin_xyz".to_string(),
            ..Default::default()
        };
        let manager = ProviderManager::new(&config);

        let res = manager.resolve("fake_non_existent_track_99999999").await;
        assert!(res.is_err());
        match res.unwrap_err() {
            error::ProviderError::AllProvidersFailed(errs) => {
                assert!(!errs.is_empty());
            }
            other => panic!("Unexpected error type: {:?}", other),
        }
    }

    #[tokio::test]
    async fn test_health_endpoint() {
        let config = ProviderConfig::default();
        let manager = Arc::new(ProviderManager::new(&config));
        let storage = Arc::new(YukisboxStorage::new(None, 30).unwrap());
        let coordinator = Arc::new(AcquisitionCoordinator::new(
            (*manager).clone(),
            (*storage).clone(),
            None,
            None,
        ));
        let state = AppState::new(manager, storage, coordinator);
        let app = build_router(state);

        let response = app
            .oneshot(
                Request::builder()
                    .uri("/health")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn test_internal_auth_middleware_blocks_unauthorized() {
        let config = ProviderConfig::default();
        let manager = Arc::new(ProviderManager::new(&config));
        let storage = Arc::new(YukisboxStorage::new(None, 30).unwrap());
        let coordinator = Arc::new(AcquisitionCoordinator::new(
            (*manager).clone(),
            (*storage).clone(),
            None,
            None,
        ));
        let state = AppState::new(manager, storage, coordinator);
        let app = build_router(state);

        // Request without X-Internal-Secret
        let response = app
            .oneshot(
                Request::builder()
                    .uri("/stream/fake_track_123")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn test_internal_auth_middleware_allows_authorized() {
        std::env::set_var("INTERNAL_API_SECRET", "test_secret_123");
        let config = ProviderConfig::default();
        let manager = Arc::new(ProviderManager::new(&config));
        let storage = Arc::new(YukisboxStorage::new(None, 30).unwrap());
        let coordinator = Arc::new(AcquisitionCoordinator::new(
            (*manager).clone(),
            (*storage).clone(),
            None,
            None,
        ));
        let state = AppState::new(manager, storage, coordinator);
        let app = build_router(state);

        // Request with valid X-Internal-Secret
        let response = app
            .oneshot(
                Request::builder()
                    .uri("/stream/non_existent_track_for_auth_check")
                    .header("X-Internal-Secret", "test_secret_123")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        // Passed auth! (Gets 404 because track doesn't exist, not 403 Forbidden)
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
    }
}
