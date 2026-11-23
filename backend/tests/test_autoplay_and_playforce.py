import pytest
from unittest.mock import AsyncMock, patch
from app.config import settings

@pytest.mark.asyncio
async def test_anayaapi_header_and_query_authentication(client):
    # 1. Generate active API key
    gen_resp = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 11223344},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert gen_resp.status_code == 200
    api_key = gen_resp.json()["api_key"]

    # Test auth via ANAYAAPI header
    resp1 = await client.get("/v1/search?q=believer", headers={"ANAYAAPI": api_key})
    assert resp1.status_code == 200

    # Test auth via anayaapi query parameter
    resp2 = await client.get(f"/v1/search?q=believer&anayaapi={api_key}")
    assert resp2.status_code == 200

@pytest.mark.asyncio
async def test_recommendations_endpoint(client):
    gen_resp = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 11223355},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    api_key = gen_resp.json()["api_key"]

    mock_recos = [
        {
            "track_id": "kiwi_saavn_reco1",
            "title": "Thunder",
            "artist": "Imagine Dragons",
            "album": "Evolve",
            "duration": 187,
            "thumbnail": "https://c.saavncdn.com/reco1.jpg",
            "source_url": "https://aac.saavncdn.com/reco1.mp4",
            "audio_format": "mp4"
        },
        {
            "track_id": "kiwi_saavn_reco2",
            "title": "Natural",
            "artist": "Imagine Dragons",
            "album": "Origins",
            "duration": 189,
            "thumbnail": "https://c.saavncdn.com/reco2.jpg",
            "source_url": "https://aac.saavncdn.com/reco2.mp4",
            "audio_format": "mp4"
        }
    ]

    with patch("app.services.music.music_service.provider.get_recommendations", AsyncMock(return_value=mock_recos)):
        resp = await client.get("/v1/recommendations/kiwi_saavn_seed", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["count"] == 2
        assert data["results"][0]["title"] == "Thunder"
        assert data["results"][1]["title"] == "Natural"

@pytest.mark.asyncio
async def test_autoplay_endpoint(client):
    gen_resp = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 11223366},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    api_key = gen_resp.json()["api_key"]

    mock_recos = [
        {
            "track_id": "kiwi_saavn_auto1",
            "title": "Radioactive",
            "artist": "Imagine Dragons",
            "album": "Night Visions",
            "duration": 186,
            "thumbnail": "https://c.saavncdn.com/auto1.jpg",
            "source_url": "https://aac.saavncdn.com/auto1.mp4",
            "audio_format": "mp4"
        }
    ]

    mock_resolved = {
        "track_id": "kiwi_saavn_auto1",
        "title": "Radioactive",
        "artist": "Imagine Dragons",
        "storage": "jiosaavn_direct",
        "storage_uid": "auto1",
        "music_url": "https://aac.saavncdn.com/auto1.mp4",
        "cached": False,
        "duration": 186
    }

    with patch("app.services.music.music_service.provider.get_recommendations", AsyncMock(return_value=mock_recos)), \
         patch("app.services.music.music_service.resolve", AsyncMock(return_value=mock_resolved)):

        resp = await client.get("/v1/autoplay/kiwi_saavn_auto_seed", headers={"ANAYAAPI": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["track_id"] == "kiwi_saavn_auto1"
        assert data["title"] == "Radioactive"
        assert data["music_url"] == "https://aac.saavncdn.com/auto1.mp4"
        assert len(data["recommendations"]) == 1

@pytest.mark.asyncio
async def test_playforce_endpoints(client):
    gen_resp = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 11223377},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    api_key = gen_resp.json()["api_key"]

    mock_resolved = {
        "track_id": "kiwi_saavn_force1",
        "title": "Demons",
        "artist": "Imagine Dragons",
        "storage": "jiosaavn_direct",
        "storage_uid": "force1",
        "music_url": "https://aac.saavncdn.com/force1.mp4",
        "cached": False,
        "duration": 177
    }

    with patch("app.services.music.music_service.resolve", AsyncMock(return_value=mock_resolved)):
        # Test GET /v1/playforce/{target}
        resp1 = await client.get("/v1/playforce/kiwi_saavn_force1", headers={"ANAYAAPI": api_key})
        assert resp1.status_code == 200
        d1 = resp1.json()
        assert d1["success"] is True
        assert d1["force"] is True
        assert d1["music_url"] == "https://aac.saavncdn.com/force1.mp4"

        # Test POST /v1/playforce
        resp2 = await client.post(
            "/v1/playforce",
            json={"track_id": "kiwi_saavn_force1"},
            headers={"X-API-Key": api_key}
        )
        assert resp2.status_code == 200
        d2 = resp2.json()
        assert d2["success"] is True
        assert d2["force"] is True

        # Test GET /v1/resolve/{track_id}?force=true
        resp3 = await client.get("/v1/resolve/kiwi_saavn_force1?force=true", headers={"X-API-Key": api_key})
        assert resp3.status_code == 200
        d3 = resp3.json()
        assert d3["success"] is True
