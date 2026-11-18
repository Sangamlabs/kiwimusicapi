import pytest
from unittest.mock import AsyncMock, patch
from app.services.jiosaavn import JioSaavnProvider

@pytest.mark.asyncio
async def test_jiosaavn_empty_search():
    provider = JioSaavnProvider()
    results = await provider.search("")
    assert results == []

@pytest.mark.asyncio
async def test_jiosaavn_search_mock():
    provider = JioSaavnProvider()
    raw_mock = {
        "results": [
            {
                "id": "12345",
                "song": "Test Track",
                "primary_artists": "Artist A",
                "album": "Album X",
                "duration": "180",
                "media_preview_url": "https://preview.saavncdn.com/test.mp4"
            }
        ]
    }
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = AsyncMock(status_code=200, json=lambda: raw_mock)
        results = await provider.search("Test Track", limit=5)
        assert len(results) == 1
        item = results[0]
        assert item["track_id"] == "kiwi_saavn_12345"
        assert item["title"] == "Test Track"
        assert item["artist"] == "Artist A"
        assert item["duration"] == 180
        assert item["source_url"] == "https://preview.saavncdn.com/test.mp4"

@pytest.mark.asyncio
async def test_jiosaavn_get_track_mock():
    provider = JioSaavnProvider()
    raw_mock = {
        "12345": {
            "id": "12345",
            "song": "Detailed Song",
            "singers": "Singer B",
            "album": "Great Album",
            "duration": "210",
            "media_preview_url": "https://preview.saavncdn.com/sample.mp4"
        }
    }
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = AsyncMock(status_code=200, json=lambda: raw_mock)
        track = await provider.get_track("kiwi_saavn_12345")
        assert track is not None
        assert track["track_id"] == "kiwi_saavn_12345"
        assert track["title"] == "Detailed Song"
        assert track["duration"] == 210

