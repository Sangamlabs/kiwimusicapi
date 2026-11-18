import asyncio
import base64
import html
import logging
import re
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

    @staticmethod
    def _get_tokens(text: str) -> set[str]:
        words = re.findall(r"[a-zA-Z0-9]+", text.lower())
        stop_words = {
            "the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "of", "with", "by",
            "ft", "feat", "song", "video", "official", "4k", "8k", "hd", "1080p", "720p",
            "lyrics", "lyric", "audio", "full", "visualizer", "edit", "amv", "remix", "version",
            "records", "music"
        }
        return {w for w in words if w not in stop_words and len(w) > 1}

    @classmethod
    def _score_match(cls, yt_title: str, item: dict[str, Any]) -> float:
        """
        Calculate matching confidence score between a YouTube title and a JioSaavn song.
        Returns float between 0.0 and 2.0.
        """
        yt_tokens = cls._get_tokens(yt_title)
        if not yt_tokens:
            return 0.0

        song_title = item.get("song", "") or item.get("title", "")
        song_tokens = cls._get_tokens(song_title)
        if not song_tokens:
            return 0.0

        # Title token match ratio
        matched_title = yt_tokens.intersection(song_tokens)
        title_ratio = len(matched_title) / len(song_tokens)

        # Single word titles need higher verification (e.g. "Revenge")
        if len(song_tokens) == 1:
            title_ratio = 0.50 if title_ratio == 1.0 else 0.0

        # Artist match bonus
        artist_text = f"{item.get('singers', '')} {item.get('primary_artists', '')} {item.get('artist', '')}"
        artist_tokens = cls._get_tokens(artist_text)
        artist_match_count = len(yt_tokens.intersection(artist_tokens))
        artist_bonus = min(0.40, artist_match_count * 0.20)

        # Album match bonus
        album_text = item.get("album", "")
        album_tokens = cls._get_tokens(album_text)
        album_match_count = len(yt_tokens.intersection(album_tokens))
        album_bonus = min(0.20, album_match_count * 0.10)

        return title_ratio + artist_bonus + album_bonus

    @classmethod
    def _generate_search_candidates(cls, raw_title: str) -> list[str]:
        """
        Generate cleaned, progressive search candidates from raw titles (e.g. YouTube oEmbed titles).
        Strips video tags, parentheticals, feat/ft tokens, emojis, and noisy channel labels.
        """
        candidates: list[str] = []

        def add_candidate(c: str):
            clean = re.sub(r"\s+", " ", c).strip(" -|:_#~!/\\")
            if clean and len(clean) >= 2 and clean not in candidates:
                candidates.append(clean)

        noise_pattern = (
            r"(?i)\b(4k|8k|hd|1080p|720p|official music video|official video|full video song|"
            r"full video|video song|music video|official audio|lyric video|lyrics video|"
            r"lyrics|audio song|full song|visualizer|tiktok version|tiktok remix|edit audio|"
            r"edit|amv|video|audio|song)\b"
        )

        # 1. Clean bracketed annotations and noise from raw title
        no_brackets = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", raw_title)
        clean_raw = re.sub(noise_pattern, " ", no_brackets)
        clean_raw = re.sub(r"[^\x00-\x7F]+", " ", clean_raw)

        # 2. Extract delimited components (often: [Song Name] | [Album/Movie] | [Artists])
        raw_parts = re.split(r"\||//|::", raw_title)
        clean_parts: list[str] = []
        for p in raw_parts:
            cp = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", p)
            cp = re.sub(noise_pattern, " ", cp)
            cp = re.sub(r"[^\x00-\x7F]+", " ", cp).strip(" -|:")
            if len(cp) >= 2:
                clean_parts.append(cp)

        if len(clean_parts) >= 2:
            # Combine Song Name + Movie/Album: e.g. "Aaja Mahi Singh Is Bliing"
            add_candidate(f"{clean_parts[0]} {clean_parts[1]}")
            # Just Song Name: e.g. "Aaja Mahi"
            add_candidate(clean_parts[0])
            # If 3 or more parts, try Song Name + Artist: e.g. "Aaja Mahi Arijit Singh"
            for extra in clean_parts[2:]:
                add_candidate(f"{clean_parts[0]} {extra}")

        # 3. Clean raw cut before ft./feat.
        no_ft = re.sub(r"(?i)\b(ft\.?|feat\.?).*$", "", clean_raw).strip(" -|:")
        add_candidate(no_ft)

        # 4. Clean raw without noise
        add_candidate(clean_raw)

        # 5. Fallback: original raw title
        add_candidate(raw_title)

        return candidates

    async def get_track(self, track_id: str) -> dict[str, Any] | None:
        """
        Fetch song details by ID.
        Supports:
          1. JioSaavn song ID / kiwi_saavn_<id>
          2. YouTube Video ID (e.g. 11 characters like _UY8JhRlcgE) via oEmbed -> JioSaavn match
          3. Title fallback search
        """
        song_id = track_id.replace("kiwi_saavn_", "").strip()
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
                # 1. Direct song details call on JioSaavn
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
                        normalized = self._normalize_item(item)
                        normalized["track_id"] = track_id
                        return normalized

                # 2. Check if track_id is a YouTube ID (11 chars, or starts with yt_)
                clean_id = song_id.replace("yt_", "").replace("youtube_", "")
                if len(clean_id) == 11 or "youtu" in clean_id:
                    oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={clean_id}&format=json"
                    yt_resp = await client.get(oembed_url)
                    if yt_resp.status_code == 200:
                        yt_data = yt_resp.json()
                        yt_title = yt_data.get("title", "").strip()
                        if yt_title:
                            best_match = None
                            best_score = 0.0

                            for candidate in self._generate_search_candidates(yt_title):
                                search_results = await self.search(candidate, limit=5)
                                for item in search_results:
                                    score = self._score_match(yt_title, item)
                                    if score > best_score:
                                        best_score = score
                                        best_match = item
                                if best_score >= 1.0:
                                    break

                            # Verify confidence threshold (>= 0.70)
                            # Prevents returning random wrong tracks (e.g. devotional song for Bollywood)
                            if best_match and best_score >= 0.70:
                                best_match["track_id"] = track_id
                                best_match["provider_song_id"] = clean_id
                                logger.info(
                                    "Matched YouTube ID %s ('%s') with JioSaavn '%s' (score=%.2f)",
                                    clean_id, yt_title, best_match.get("title"), best_score
                                )
                                return best_match
                            else:
                                logger.warning(
                                    "No confident match for YouTube ID %s ('%s', best_score=%.2f). Rejecting fuzzy match to avoid wrong song.",
                                    clean_id, yt_title, best_score
                                )
                                return None

                # 3. Fallback: try searching JioSaavn with track_id string if non-numeric
                if len(song_id) > 2 and not song_id.isdigit():
                    best_match = None
                    best_score = 0.0
                    for candidate in self._generate_search_candidates(song_id):
                        search_results = await self.search(candidate, limit=3)
                        for item in search_results:
                            score = self._score_match(song_id, item)
                            if score > best_score:
                                best_score = score
                                best_match = item
                        if best_score >= 1.0:
                            break

                    if best_match and best_score >= 0.70:
                        best_match["track_id"] = track_id
                        return best_match

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

