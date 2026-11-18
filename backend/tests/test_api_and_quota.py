import pytest
from unittest.mock import AsyncMock, patch
from app.config import settings

@pytest.mark.asyncio
async def test_health_endpoint(client):
    resp = await client.get("/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "database" in data

@pytest.mark.asyncio
async def test_auth_missing_key(client):
    resp = await client.get("/v1/search?q=test")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "MISSING_API_KEY"

@pytest.mark.asyncio
async def test_auth_invalid_key(client):
    resp = await client.get("/v1/search?q=test", headers={"X-API-Key": "KIWI-INVALIDKEY123"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_API_KEY"

@pytest.mark.asyncio
async def test_key_generation_and_quota_lifecycle(client):
    # 1. Generate key via internal endpoint
    gen_resp = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 999888777},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert gen_resp.status_code == 200
    gen_data = gen_resp.json()
    assert gen_data["success"] is True
    api_key = gen_data["api_key"]
    prefix = gen_data["key_prefix"]

    # 2. Free endpoint (Search): does NOT consume quota
    with patch("app.services.music.music_service.search", return_value=[]):
        search_resp = await client.get("/v1/search?q=test", headers={"X-API-Key": api_key})
        assert search_resp.status_code == 200

    # Verify usage is still 0 used
    usage_resp = await client.get("/v1/usage", headers={"X-API-Key": api_key})
    assert usage_resp.status_code == 200
    assert usage_resp.json()["used_requests"] == 0
    assert usage_resp.json()["remaining_requests"] == 200

    # 3. Billable endpoint (Resolve): Consumes 1 quota unit on success
    mock_resolve = {
        "track_id": "kiwi_saavn_999",
        "title": "Test Song",
        "artist": "Test Artist",
        "storage": "yukisbox",
        "storage_uid": "test_uid_999",
        "music_url": "https://yukiapi.site/file/test_uid_999",
        "cached": False,
        "duration": 180
    }
    with patch("app.services.music.music_service.resolve", return_value=mock_resolve):
        res_resp = await client.get("/v1/resolve/kiwi_saavn_999", headers={"X-API-Key": api_key})
        assert res_resp.status_code == 200
        assert res_resp.json()["music_url"] == "https://yukiapi.site/file/test_uid_999"

    # Verify quota decremented by exactly 1
    usage_resp2 = await client.get("/v1/usage", headers={"X-API-Key": api_key})
    assert usage_resp2.json()["used_requests"] == 1
    assert usage_resp2.json()["remaining_requests"] == 199

    # 4. Failed request does NOT consume quota
    with patch("app.services.music.music_service.resolve", side_effect=Exception("Provider timeout")):
        fail_resp = await client.get("/v1/resolve/kiwi_saavn_failed", headers={"X-API-Key": api_key})
        assert fail_resp.status_code == 500

    usage_resp3 = await client.get("/v1/usage", headers={"X-API-Key": api_key})
    assert usage_resp3.json()["used_requests"] == 1  # Unchanged!
    assert usage_resp3.json()["failed_requests"] == 1
    assert usage_resp3.json()["remaining_requests"] == 199

    # 5. Revoke key
    rev_resp = await client.post(
        "/internal/api-keys/revoke",
        json={"telegram_user_id": 999888777, "key_prefix": prefix},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert rev_resp.status_code == 200

    # Request with revoked key must now be rejected
    rejected_resp = await client.get("/v1/search?q=test", headers={"X-API-Key": api_key})
    assert rejected_resp.status_code == 401
    assert rejected_resp.json()["error"]["code"] == "KEY_REVOKED"

