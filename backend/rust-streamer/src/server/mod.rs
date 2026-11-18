pub mod auth;
pub mod stream;

use axum::{
    middleware,
    routing::{get, post},
    Router,
};
use std::net::SocketAddr;
use tracing::info;

pub use auth::require_internal_auth;
pub use stream::{health_handler, internal_acquire_handler, stream_handler, AppState};

pub fn build_router(state: AppState) -> Router {
    let internal_routes = Router::new()
        .route("/stream/{track_id}", get(stream_handler))
        .route("/internal/acquire", post(internal_acquire_handler))
        .layer(middleware::from_fn(require_internal_auth));

    Router::new()
        .route("/health", get(health_handler))
        .merge(internal_routes)
        .with_state(state)
}

pub async fn start_server(
    state: AppState,
    port: u16,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let router = build_router(state);
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    info!("Starting Kiwi Rust Streaming Engine on http://{}", addr);

    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, router).await?;
    Ok(())
}
