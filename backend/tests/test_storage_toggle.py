import pytest
from unittest.mock import AsyncMock, patch
from app.config import settings

@pytest.mark.asyncio
async def test_resolve_storage_upload_disabled_returns_direct_url(client):
    # 1. Generate active API key
    gen_resp = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 99887711},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert gen_resp.status_code == 200
    api_key = gen_resp.json()["api_key"]
    headers = {"X-API-Key": api_key}

    mock_track = {
        "track_id": "anaya_toggle_test_01",
        "provider": "jiosaavn",
        "provider_song_id": "song_01",
        "title": "Believer",
        "artist": "Imagine Dragons",
        "album": "Evolve",
        "duration": 204,
        "source_url": "https://aac.saavncdn.com/test_believer.mp4",
        "audio_format": "mp4",
    }

    mock_provider = AsyncMock()
    mock_provider.get_track = AsyncMock(return_value=mock_track)

    mock_storage = AsyncMock()

    with patch("app.services.music.settings.STORAGE_UPLOAD_ENABLED", False), \
         patch("app.services.music.settings.RUST_MEDIA_ENGINE_URL", ""), \
         patch("app.services.music.music_service.provider", mock_provider), \
         patch("app.services.music.music_service.storage", mock_storage):

        resp = await client.get("/v1/resolve/anaya_toggle_test_01", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["music_url"] == "https://aac.saavncdn.com/test_believer.mp4"
        assert data["storage"] == "jiosaavn_direct"
        assert not mock_storage.upload_url.called
        assert not mock_storage.upload_file.called

@pytest.mark.asyncio
async def test_resolve_storage_upload_failure_falls_back_to_direct_url(client):
    # 1. Generate active API key
    gen_resp = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 99887722},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert gen_resp.status_code == 200
    api_key = gen_resp.json()["api_key"]
    headers = {"X-API-Key": api_key}

    mock_track = {
        "track_id": "anaya_toggle_fail_02",
        "provider": "jiosaavn",
        "provider_song_id": "song_02",
        "title": "Bones",
        "artist": "Imagine Dragons",
        "album": "Mercury",
        "duration": 165,
        "source_url": "https://aac.saavncdn.com/test_bones.mp4",
        "audio_format": "mp4",
    }

    mock_provider = AsyncMock()
    mock_provider.get_track = AsyncMock(return_value=mock_track)

    mock_storage = AsyncMock()
    mock_storage.upload_url = AsyncMock(side_effect=Exception("524 Gateway Timeout"))
    mock_storage.upload_file = AsyncMock(side_effect=Exception("Connection refused"))

    with patch("app.services.music.settings.STORAGE_UPLOAD_ENABLED", True), \
         patch("app.services.music.settings.RUST_MEDIA_ENGINE_URL", ""), \
         patch("app.services.music.music_service.provider", mock_provider), \
         patch("app.services.music.music_service.storage", mock_storage):

        resp = await client.get("/v1/resolve/anaya_toggle_fail_02", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["music_url"] == "https://aac.saavncdn.com/test_bones.mp4"
        assert data["storage"] == "jiosaavn_direct"
