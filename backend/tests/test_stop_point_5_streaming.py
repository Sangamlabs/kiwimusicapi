import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
from app.config import settings

@pytest.mark.asyncio
async def test_streaming_endpoint_range_support_and_quota(client):
    # 1. Generate active API key
    gen_resp = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 777666555},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert gen_resp.status_code == 200
    api_key = gen_resp.json()["api_key"]
    headers = {
        "X-API-Key": api_key,
        "Range": "bytes=0-1023"
    }

    track_id = "kiwi_stream_range_test_01"

    # Mock Rust streaming response (206 Partial Content)
    mock_rust_response = AsyncMock()
    mock_rust_response.status_code = 206
    mock_rust_response.headers = {
        "content-type": "audio/mp4",
        "content-length": "1024",
        "content-range": "bytes 0-1023/5242880",
        "accept-ranges": "bytes",
        "cache-control": "public, max-age=86400"
    }

    async def mock_aiter_bytes():
        yield b"A" * 512
        yield b"B" * 512

    mock_rust_response.aiter_bytes = mock_aiter_bytes
    mock_rust_response.aclose = AsyncMock()

    mock_inner_client = AsyncMock()
    mock_inner_client.build_request = MagicMock()
    mock_inner_client.send = AsyncMock(return_value=mock_rust_response)
    mock_inner_client.aclose = AsyncMock()

    with patch("app.api.routes.httpx.AsyncClient", return_value=mock_inner_client):
        resp = await client.get(f"/v1/stream/{track_id}", headers=headers)
        assert resp.status_code == 206
        assert resp.headers.get("content-type") == "audio/mp4"
        assert resp.headers.get("content-range") == "bytes 0-1023/5242880"
        assert resp.headers.get("accept-ranges") == "bytes"
        assert resp.content == b"A" * 512 + b"B" * 512

    # Verify quota was atomically deducted by exactly 1 unit
    usage_resp = await client.get("/v1/usage", headers={"X-API-Key": api_key})
    assert usage_resp.status_code == 200
    usage_data = usage_resp.json()
    assert usage_data["used_requests"] == 1
    assert usage_data["remaining_requests"] == 199

@pytest.mark.asyncio
async def test_streaming_endpoint_invalid_range_416(client):
    gen_resp = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 777666556},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    api_key = gen_resp.json()["api_key"]
    headers = {
        "X-API-Key": api_key,
        "Range": "bytes=999999999-"
    }

    track_id = "kiwi_stream_range_test_416"

    mock_rust_response = AsyncMock()
    mock_rust_response.status_code = 416
    mock_rust_response.headers = {}
    mock_rust_response.aclose = AsyncMock()

    mock_inner_client = AsyncMock()
    mock_inner_client.build_request = MagicMock()
    mock_inner_client.send = AsyncMock(return_value=mock_rust_response)
    mock_inner_client.aclose = AsyncMock()

    with patch("app.api.routes.httpx.AsyncClient", return_value=mock_inner_client):
        resp = await client.get(f"/v1/stream/{track_id}", headers=headers)
        assert resp.status_code == 416

    # Verify quota is NOT deducted for 416 Range Not Satisfiable
    usage_resp = await client.get("/v1/usage", headers={"X-API-Key": api_key})
    assert usage_resp.json()["used_requests"] == 0

@pytest.mark.asyncio
async def test_streaming_fallback_when_rust_down(client):
    # Tests that when Rust engine is unreachable, Python transparently falls back to Yukysbox resolve
    gen_resp = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 777666557},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    api_key = gen_resp.json()["api_key"]
    headers = {"X-API-Key": api_key}
    track_id = "kiwi_stream_fallback_test"

    mock_resolved = {
        "track_id": track_id,
        "title": "Fallback Song",
        "artist": "Fallback Artist",
        "storage": "yukisbox",
        "storage_uid": "fb_uid_123",
        "music_url": "https://yukiapi.site/file/fb_uid_123",
        "cached": True,
        "duration": 200
    }

    mock_inner_client = AsyncMock()
    mock_inner_client.build_request = MagicMock()
    mock_inner_client.send = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    with patch("app.api.routes.httpx.AsyncClient", return_value=mock_inner_client):
        with patch("app.services.music.music_service.resolve", return_value=mock_resolved):
            resp = await client.get(f"/v1/stream/{track_id}", headers=headers, follow_redirects=False)
            assert resp.status_code == 302
            assert resp.headers.get("location") == "https://yukiapi.site/file/fb_uid_123"

    # Verify quota was consumed on successful fallback resolution
    usage_resp = await client.get("/v1/usage", headers={"X-API-Key": api_key})
    assert usage_resp.json()["used_requests"] == 1
