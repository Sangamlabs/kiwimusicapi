import asyncio
import time
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from app.models import ApiKey, Track, UploadJob, User
from app.services.music import MusicService
from app.services.cache import cache

@pytest.mark.asyncio
async def test_cache_hit_returns_fast_without_storage_or_provider(db_session):
    service = MusicService()
    track_id = "kiwi_cache_hit_test_001"

    # Seed an available track in PostgreSQL
    seeded_track = Track(
        track_id=track_id,
        provider="jiosaavn",
        provider_song_id="seed_001",
        title="Seeded Hit Song",
        artist="Seeded Artist",
        duration=210,
        storage_provider="yukisbox",
        storage_uid="seed_uid_123",
        storage_url="https://yukiapi.site/file/seed_uid_123",
        file_size=4194304,
        audio_format="mp3",
        status="available"
    )
    db_session.add(seeded_track)
    await db_session.commit()

    # Clear in-memory cache to force DB lookup
    cache.delete(f"resolve:{track_id}")

    # Track calls to ensure provider and storage are NEVER called on cache hit
    provider_mock = AsyncMock()
    storage_mock = AsyncMock()

    with patch.object(service, "provider", provider_mock):
        with patch.object(service, "storage", storage_mock):
            start = time.perf_counter()
            result = await service.resolve(track_id, db_session)
            elapsed_ms = (time.perf_counter() - start) * 1000

            assert result["cached"] is True
            assert result["storage_uid"] == "seed_uid_123"
            assert result["music_url"] == "https://yukiapi.site/file/seed_uid_123"
            assert elapsed_ms < 100  # fast DB hit

            # Provider and Storage must NOT have been called!
            provider_mock.get_track.assert_not_called()
            storage_mock.upload_url.assert_not_called()
            storage_mock.upload_file.assert_not_called()

@pytest.mark.asyncio
async def test_single_flight_lease_transitions_to_completed(db_session):
    service = MusicService()
    track_id = "kiwi_lease_transition_test_002"

    mock_provider_data = {
        "provider": "jiosaavn",
        "provider_song_id": "trans_002",
        "track_id": track_id,
        "title": "Lease Test Song",
        "artist": "Lease Artist",
        "duration": 180,
        "source_url": "https://cdn.example.com/audio.mp3",
        "audio_format": "mp3"
    }

    mock_upload = {
        "success": True,
        "uid": "lease_uid_002",
        "url": "https://yukiapi.site/file/lease_uid_002",
        "storage_provider": "yukisbox",
        "file_size": 3145728
    }

    with patch.object(service.provider, "get_track", return_value=mock_provider_data):
        with patch.object(service.storage, "upload_url", return_value=mock_upload):
            res = await service.resolve(track_id, db_session)
            assert res["cached"] is False
            assert res["storage_uid"] == "lease_uid_002"

            # Verify UploadJob row exists and status is COMPLETED
            job_res = await db_session.execute(select(UploadJob).where(UploadJob.track_id == track_id))
            job = job_res.scalar_one_or_none()
            assert job is not None
            assert job.status == "COMPLETED"
            assert job.attempts >= 1
            assert job.completed_at is not None
            assert job.locked_at is None

@pytest.mark.asyncio
async def test_quota_preservation_on_failed_resolution(client):
    from app.config import settings
    # 1. Generate key via internal endpoint
    gen_resp = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 888999777},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert gen_resp.status_code == 200
    gen_data = gen_resp.json()
    api_key = gen_data["api_key"]

    headers = {"X-API-Key": api_key}

    # 2. Mock provider failure for a non-existent track
    with patch("app.services.music.music_service.resolve", side_effect=ValueError("Track not found")):
        resp = await client.get("/v1/resolve/non_existent_fail_track", headers=headers)
        assert resp.status_code == 404

    # 3. Check usage stats via /v1/usage
    usage_resp = await client.get("/v1/usage", headers=headers)
    assert usage_resp.status_code == 200
    usage_data = usage_resp.json()

    # Quota must NOT be consumed on failure!
    assert usage_data["used_requests"] == 0
    assert usage_data["failed_requests"] == 1
    assert usage_data["remaining_requests"] == 200
