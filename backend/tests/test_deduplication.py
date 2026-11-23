import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from app.models import Track
from app.services.music import MusicService

@pytest.mark.asyncio
async def test_concurrent_deduplication(db_session):
    service = MusicService()
    track_id = "kiwi_saavn_dedup_test"

    # Mock provider response
    mock_provider_data = {
        "provider": "jiosaavn",
        "provider_song_id": "dedup_test",
        "track_id": track_id,
        "title": "Dedup Song",
        "artist": "Dedup Artist",
        "duration": 200,
        "source_url": "https://cdn.example.com/audio.mp3",
        "audio_format": "mp3"
    }

    provider_call_count = 0

    async def mock_get_track(tid):
        nonlocal provider_call_count
        provider_call_count += 1
        await asyncio.sleep(0.05)  # simulate network latency
        return mock_provider_data

    with patch.object(service.provider, "get_track", side_effect=mock_get_track):
        # Run 5 concurrent resolve calls for the same uncached track
        tasks = [service.resolve(track_id, db_session) for _ in range(5)]
        results = await asyncio.gather(*tasks)

        # All 5 calls should succeed
        assert len(results) == 5
        for r in results:
            assert r["music_url"] == "https://cdn.example.com/audio.mp3"
            assert r["storage"] == "jiosaavn_direct"
            assert r["storage_uid"] == "dedup_test"

        # Verify track record exists in database
        db_tracks = (await db_session.execute(select(Track).where(Track.track_id == track_id))).scalars().all()
        assert len(db_tracks) >= 1
        assert db_tracks[0].storage_uid is not None

