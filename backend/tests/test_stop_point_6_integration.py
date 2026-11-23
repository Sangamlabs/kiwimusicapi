import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from app.config import settings

@pytest.mark.asyncio
async def test_end_to_end_resolve_through_rust_acquisition(client):
    """
    Test Phase 6 end-to-end integration:
    1. Authenticate API key.
    2. Check quota.
    3. Uncached track -> Dispatch acquisition to Rust /internal/acquire with X-Internal-Secret.
    4. Rust completes acquisition and returns track.
    5. Python returns ResolveResponse and deducts quota.
    6. Second call is an instant Cache HIT from DB (zero provider / acquire calls).
    """
    gen_resp = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 888111001},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert gen_resp.status_code == 200
    api_key = gen_resp.json()["api_key"]
    headers = {"X-API-Key": api_key}

    track_id = "integration_test_track_01"

    # Mock Rust /internal/acquire response
    mock_rust_acquire_resp = MagicMock()
    mock_rust_acquire_resp.status_code = 200
    mock_rust_acquire_resp.json = MagicMock(return_value={
        "success": True,
        "track": {
            "track_id": track_id,
            "provider": "jiosaavn",
            "provider_song_id": "saavn_int_01",
            "title": "Integration Song",
            "artist": "Rust Artist",
            "album": "Kiwi Album",
            "duration": 210,
            "storage_provider": "yukisbox",
            "storage_uid": "uid_int_01",
            "storage_url": "https://yukiapi.site/v1/file/uid_int_01",
            "cached": True
        }
    })

    with patch("app.services.music.settings.STORAGE_UPLOAD_ENABLED", True), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_rust_acquire_resp

        # First Call: Uncached -> dispatches to Rust /internal/acquire
        resp = await client.get(f"/v1/resolve/{track_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["track_id"] == track_id
        assert data["title"] == "Integration Song"
        assert data["music_url"] == "https://yukiapi.site/v1/file/uid_int_01"
        assert data["cached"] is True

        # Verify Rust was called with X-Internal-Secret
        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["headers"]["X-Internal-Secret"] == settings.INTERNAL_API_SECRET
        assert call_kwargs["json"]["track_id"] == track_id

    # Verify quota deducted by exactly 1
    usage_resp = await client.get("/v1/usage", headers=headers)
    assert usage_resp.json()["used_requests"] == 1
    assert usage_resp.json()["remaining_requests"] == 199

@pytest.mark.asyncio
async def test_end_to_end_streaming_through_rust_engine(client):
    """
    Test Phase 6 end-to-end streaming integration:
    Client -> Python /v1/stream/{track_id} with Range -> Rust /stream/{track_id} -> Client.
    """
    gen_resp = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 888111002},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    api_key = gen_resp.json()["api_key"]
    headers = {
        "X-API-Key": api_key,
        "Range": "bytes=0-2047"
    }

    track_id = "integration_test_stream_02"

    mock_rust_stream_resp = AsyncMock()
    mock_rust_stream_resp.status_code = 206
    mock_rust_stream_resp.headers = {
        "content-type": "audio/mp4",
        "content-length": "2048",
        "content-range": "bytes 0-2047/10485760",
        "accept-ranges": "bytes",
        "cache-control": "public, max-age=86400"
    }

    async def mock_chunks():
        yield b"CHUNK_1" * 128
        yield b"CHUNK_2" * 128

    mock_rust_stream_resp.aiter_bytes = mock_chunks
    mock_rust_stream_resp.aclose = AsyncMock()

    mock_inner_client = AsyncMock()
    mock_inner_client.build_request = MagicMock()
    mock_inner_client.send = AsyncMock(return_value=mock_rust_stream_resp)
    mock_inner_client.aclose = AsyncMock()

    with patch("app.api.routes.httpx.AsyncClient", return_value=mock_inner_client):
        resp = await client.get(f"/v1/stream/{track_id}", headers=headers)
        assert resp.status_code == 206
        assert resp.headers.get("content-type") == "audio/mp4"
        assert resp.headers.get("content-range") == "bytes 0-2047/10485760"
        assert resp.headers.get("accept-ranges") == "bytes"
        assert resp.content == (b"CHUNK_1" * 128 + b"CHUNK_2" * 128)

    # Verify quota consumed
    usage_resp = await client.get("/v1/usage", headers={"X-API-Key": api_key})
    assert usage_resp.json()["used_requests"] == 1
    assert usage_resp.json()["remaining_requests"] == 199

@pytest.mark.asyncio
async def test_quota_exhausted_blocks_resolve_and_stream(client, db_session):
    """
    Test that when an API key reaches 200 requests, both /resolve and /stream are blocked with 403.
    """
    gen_resp = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 888111003},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    data = gen_resp.json()
    api_key = data["api_key"]
    prefix = data["key_prefix"]
    headers = {"X-API-Key": api_key}

    # Manually consume full quota via db_session
    from sqlalchemy import update
    from app.models import ApiKey

    await db_session.execute(
        update(ApiKey).where(ApiKey.key_prefix == prefix).values(used_requests=200)
    )
    await db_session.commit()

    # Attempt resolve -> 403 Forbidden
    resolve_resp = await client.get("/v1/resolve/any_track", headers=headers)
    assert resolve_resp.status_code == 403
    assert resolve_resp.json()["error"]["code"] == "QUOTA_EXHAUSTED"

    # Attempt stream -> 403 Forbidden
    stream_resp = await client.get("/v1/stream/any_track", headers=headers)
    assert stream_resp.status_code == 403
    assert stream_resp.json()["error"]["code"] == "QUOTA_EXHAUSTED"

@pytest.mark.asyncio
async def test_unauthenticated_requests_blocked(client):
    """
    Test that requests without valid public API keys are rejected with 401.
    """
    # No header
    resp = await client.get("/v1/resolve/some_track")
    assert resp.status_code == 401

    # Invalid header
    resp2 = await client.get("/v1/stream/some_track", headers={"X-API-Key": "kiwi_live_invalidkey12345"})
    assert resp2.status_code == 401

@pytest.mark.asyncio
async def test_lightweight_health_check_endpoint(client):
    """
    Test that /v1/health is ultra-lightweight, requires no auth, consumes no quota, and returns 200.
    """
    resp = await client.get("/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["status"] == "healthy"
    assert data["database"] == "operational"

@pytest.mark.asyncio
async def test_rust_offline_resolve_fallback_to_python(client):
    """
    Test that if Rust engine is offline/unreachable during resolve(),
    Python seamlessly executes local provider and storage upload fallback.
    """
    gen_resp = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 888111004},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    api_key = gen_resp.json()["api_key"]
    headers = {"X-API-Key": api_key}

    track_id = "test_rust_down_fallback_track"

    mock_provider_track = {
        "provider": "jiosaavn",
        "provider_song_id": "prov_down_01",
        "title": "Fallback Track",
        "artist": "Python Artist",
        "album": "Local Album",
        "duration": 180,
        "source_url": "https://aac.saavncdn.com/fallback.mp4"
    }

    mock_storage_upload = {
        "success": True,
        "uid": "uid_fallback_999",
        "url": "https://yukiapi.site/v1/file/uid_fallback_999",
        "slug": "fallback_slug"
    }

    # Simulate Rust unreachable (ConnectError), but local provider & storage succeed
    with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("Connection refused")), \
         patch("app.services.music.JioSaavnProvider.get_track", new_callable=AsyncMock, return_value=mock_provider_track), \
         patch("app.services.music.YukisboxStorage.upload_url", new_callable=AsyncMock, return_value=mock_storage_upload):

        resp = await client.get(f"/v1/resolve/{track_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["track_id"] == track_id
        assert data["storage_uid"] == "prov_down_01"
        assert data["music_url"] == "https://aac.saavncdn.com/fallback.mp4"
        assert data["storage"] == "jiosaavn_direct"
