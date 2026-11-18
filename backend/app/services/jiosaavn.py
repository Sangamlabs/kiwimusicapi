import asyncio
import base64
import html
import logging
from typing import Any
from Crypto.Cipher import DES
import httpx
from app.config import settings
from app.services.providers import MusicProvider

logger = logging.getLogger(__name__)

DES_CIPHER_KEY = b"38346591"

class JioSaavnProvider(MusicProvider):
    """
    Provider adapter for JioSaavn.
    Supports cyberboysumanjay/JioSaavnAPI endpoints or direct JioSaavn gateway fallback.
    """

    def __init__(self, base_url: str | None = None, timeout: float = 15.0):
        self.base_url = (base_url or settings.JIOSAAVN_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }

    def _decrypt_media_url(self, encrypted_url: str) -> str:
        """Decrypt JioSaavn DES encrypted_media_url."""
        try:
            cipher = DES.new(DES_CIPHER_KEY, DES.MODE_ECB)
            raw_cipher = base64.b64decode(encrypted_url.strip())
            decrypted = cipher.decrypt(raw_cipher)
            pad = decrypted[-1]
            url = decrypted[:-pad].decode("utf-8")
            # Upgrade 96kbps to 160 or 320 if possible
            if "_96.mp4" in url:
                url = url.replace("_96.mp4", "_160.mp4")
            return url
        except Exception as e:
            logger.warning("Failed to decrypt JioSaavn media url: %s", e)
            return ""

    def _clean_text(self, text: str | None) -> str:
        if not text:
            return ""
        return html.unescape(text).strip()

    def _normalize_item(self, item: dict[str, Any]) -> dict[str, Any]:
        """Normalize raw JioSaavn item into standard Kiwi track format."""
        song_id = str(item.get("id") or item.get("song_id") or "")
        title = self._clean_text(item.get("song") or item.get("title") or "Unknown Title")
        artist = self._clean_text(
            item.get("primary_artists") or item.get("singers") or item.get("music") or "Unknown Artist"
        )
        album = self._clean_text(item.get("album") or "")

        try:
            duration = int(item.get("duration") or 0)
        except (ValueError, TypeError):
            duration = 0

        # Thumbnail
        image = item.get("image") or ""
        if isinstance(image, str):
            image = image.replace("50x50", "500x500").replace("150x150", "500x500")

        # Media URL
        audio_url = ""
        encrypted_url = item.get("encrypted_media_url")
        if encrypted_url:
            audio_url = self._decrypt_media_url(encrypted_url)
        if not audio_url:
            audio_url = item.get("media_preview_url") or item.get("media_url") or item.get("url") or ""

        return {
            "provider": "jiosaavn",
            "provider_song_id": song_id,
            "track_id": f"kiwi_saavn_{song_id}",
            "title": title,
            "artist": artist,
            "album": album,
            "duration": duration,
            "language": item.get("language") or "Hindi",
            "thumbnail": image,
            "source_url": audio_url,
            "audio_format": "m4a" if ".mp4" in audio_url or ".m4a" in audio_url else "mp3"
        }

    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search JioSaavn for tracks matching query."""
        if not query or not query.strip():
            return []

        clean_query = query.strip()
        attempts = 0
        max_attempts = 2
        delay = 0.5

        while attempts < max_attempts:
            attempts += 1
            try:
                async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
                    # 1. Try custom Sumanjay API base URL if configured
                    if self.base_url:
                        url = f"{self.base_url}/result/?query={clean_query}"
                        resp = await client.get(url)
                        if resp.status_code == 200:
                            data = resp.json()
                            if isinstance(data, list):
                                return [self._normalize_item(i) for i in data[:limit]]
                            if isinstance(data, dict) and "results" in data:
                                return [self._normalize_item(i) for i in data["results"][:limit]]

                    # 2. Native direct JioSaavn endpoint
                    params = {
                        "__call": "search.getResults",
                        "_format": "json",
                        "n": str(min(limit, 20)),
                        "p": "1",
                        "q": clean_query,
                    }
                    resp = await client.get("https://www.jiosaavn.com/api.php", params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        results = data.get("results") or []
                        return [self._normalize_item(i) for i in results[:limit]]

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                logger.warning("JioSaavn search attempt %d failed: %s", attempts, e)
                if attempts < max_attempts:
                    await asyncio.sleep(delay)
                    delay *= 2
            except Exception as e:
                logger.error("Unexpected error in JioSaavn search: %s", e)
                break

        return []

    async def get_track(self, track_id: str) -> dict[str, Any] | None:
        """Fetch song details by ID."""
        song_id = track_id.replace("kiwi_saavn_", "")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
                # Direct song details call
                params = {
                    "__call": "song.getDetails",
                    "cc": "in",
                    "_marker": "0",
                    "_format": "json",
                    "pids": song_id
                }
                resp = await client.get("https://www.jiosaavn.com/api.php", params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    item = data.get(song_id) or (data.get("songs") and data["songs"][0])
                    if item:
                        return self._normalize_item(item)
        except Exception as e:
            logger.error("Error fetching JioSaavn track %s: %s", track_id, e)

        return None

    async def get_audio_source(self, track_id: str) -> dict[str, Any] | None:
        """Obtain direct media URL for track."""
        track = await self.get_track(track_id)
        if track and track.get("source_url"):
            return {
                "source_url": track["source_url"],
                "audio_format": track.get("audio_format", "mp3"),
                "duration": track.get("duration", 0)
            }
        return None

