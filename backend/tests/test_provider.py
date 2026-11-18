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

def test_generate_search_candidates():
    title = "Gal Sun - Sabaat Batin ft. Rackstar | Official Music Video | SkillMill Records"
    candidates = JioSaavnProvider._generate_search_candidates(title)
    assert len(candidates) >= 3
    # Check that pipe suffix was removed
    assert any("Official Music Video" not in c for c in candidates)
    # Check that ft. was cleaned
    assert any("Gal Sun - Sabaat Batin" in c for c in candidates)

def test_score_match():
    # Accurate Bollywood song match with artist and album
    yt_title = "Aaja Mahi Aaja Mahi 4K Video Song | Singh Is Bliing | Akshay Kumar, Amy Jackson | Arijit Singh Song"
    good_item = {
        "song": "Mahi Aaja",
        "singers": "Arijit Singh",
        "album": "Singh Is Bliing"
    }
    score_good = JioSaavnProvider._score_match(yt_title, good_item)
    assert score_good >= 0.70

    # Unrelated devotional song with noise words
    bad_item = {
        "song": "Aaja Maa Aaja",
        "singers": "Narendra Chanchal",
        "album": "Mata Ki Bheint"
    }
    score_bad = JioSaavnProvider._score_match(yt_title, bad_item)
    assert score_bad < 0.70

    # Phonk track vs Punjabi single-word song
    phonk_yt = "REVENGE - 1HXSX (Brazilian Phonk)"
    punjabi_item = {
        "song": "Revenge",
        "singers": "Mankirt Aulakh",
        "album": "Revenge"
    }
    score_phonk = JioSaavnProvider._score_match(phonk_yt, punjabi_item)
    assert score_phonk < 0.70

@pytest.mark.asyncio
async def test_get_track_rejects_low_confidence_match():
    provider = JioSaavnProvider()
    with patch("httpx.AsyncClient.get") as mock_get:
        # Mock YouTube oEmbed returning a phonk title
        yt_resp = AsyncMock(status_code=200, json=lambda: {"title": "REVENGE - 1HXSX (Brazilian Phonk)"})
        # Mock JioSaavn search returning Mankirt Aulakh's Revenge
        jio_resp = AsyncMock(status_code=200, json=lambda: {
            "results": [
                {
                    "id": "99999",
                    "song": "Revenge",
                    "primary_artists": "Mankirt Aulakh",
                    "album": "Revenge",
                    "duration": "120",
                    "media_preview_url": "https://preview.saavncdn.com/revenge.mp4"
                }
            ]
        })
        mock_get.side_effect = [yt_resp, jio_resp, jio_resp, jio_resp, jio_resp, jio_resp]

        # Should return None because confidence score is below 0.70
        track = await provider.get_track("mbSp4gsKaU0")
        assert track is None



