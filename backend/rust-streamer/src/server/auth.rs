use axum::{
    extract::Request,
    http::{HeaderMap, StatusCode},
    middleware::Next,
    response::{IntoResponse, Json, Response},
};
use serde_json::json;

pub async fn require_internal_auth(headers: HeaderMap, request: Request, next: Next) -> Response {
    let expected_secret = std::env::var("INTERNAL_API_SECRET")
        .unwrap_or_else(|_| "kiwi_secret_change_me_in_production".to_string());

    let provided_secret = headers
        .get("x-internal-secret")
        .and_then(|v| v.to_str().ok());

    match provided_secret {
        Some(secret) if secret == expected_secret => next.run(request).await,
        _ => (
            StatusCode::FORBIDDEN,
            Json(json!({
                "success": false,
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Invalid or missing internal authorization secret"
                }
            })),
        )
            .into_response(),
    }
}
