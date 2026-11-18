import os
import pytest
from unittest.mock import AsyncMock, patch
from app.services.yukysbox import YukisboxStorage

@pytest.mark.asyncio
async def test_storage_ssrf_protection():
    storage = YukisboxStorage()
    assert storage._is_safe_url("http://127.0.0.1/audio.mp3") is False
    assert storage._is_safe_url("http://192.168.1.5/audio.mp3") is False
    assert storage._is_safe_url("http://10.0.0.1/audio.mp3") is False
    assert storage._is_safe_url("ftp://example.com/audio.mp3") is False
    assert storage._is_safe_url("https://aac.saavncdn.com/song.mp4") is True

@pytest.mark.asyncio
async def test_storage_get_file_info_existing():
    storage = YukisboxStorage()
    mock_resp = {
        "uid": "test-slug-123",
        "size": 54321,
        "mimetype": "audio/mpeg",
        "filename": "song.mp3"
    }

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = AsyncMock(status_code=200, json=lambda: mock_resp)
        info = await storage.get_file_info("test-slug-123")
        assert info is not None
        assert info["uid"] == "test-slug-123"
        assert info["size"] == 54321

@pytest.mark.asyncio
async def test_storage_upload_url_reuse_existing():
    storage = YukisboxStorage()
    existing_mock = {
        "uid": "my-song-slug",
        "size": 100000,
        "filename": "my-song.mp3"
    }

    with patch.object(storage, "get_file_info", return_value=existing_mock):
        res = await storage.upload_url("https://cdn.example.com/song.mp3", custom_slug="my-song-slug")
        assert res["success"] is True
        assert res["reused"] is True
        assert res["uid"] == "my-song-slug"
        assert "https://yukiapi.site/file/my-song-slug" in res["url"]

@pytest.mark.asyncio
async def test_storage_upload_url_success():
    storage = YukisboxStorage()
    with patch.object(storage, "get_file_info", return_value=None):
        mock_upload_resp = {
            "success": True,
            "uid": "new-song-slug",
            "url": "https://yukiapi.site/file/new-song-slug"
        }
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = AsyncMock(status_code=200, json=lambda: mock_upload_resp)
            res = await storage.upload_url("https://cdn.example.com/song.mp3", custom_slug="new-song-slug")
            assert res["success"] is True
            assert res["reused"] is False
            assert res["uid"] == "new-song-slug"

@pytest.mark.asyncio
async def test_storage_upload_file_fallback(tmp_path):
    storage = YukisboxStorage()
    test_file = tmp_path / "audio.mp3"
    test_file.write_bytes(b"dummy audio binary data")

    with patch.object(storage, "get_file_info", return_value=None):
        mock_resp = {
            "success": True,
            "uid": "local-upload-slug",
            "url": "https://yukiapi.site/file/local-upload-slug"
        }
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = AsyncMock(status_code=200, json=lambda: mock_resp)
            res = await storage.upload_file(str(test_file), custom_slug="local-upload-slug")
            assert res["success"] is True
            assert res["uid"] == "local-upload-slug"

