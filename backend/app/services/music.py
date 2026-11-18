import asyncio
from datetime import datetime
import logging
import os
import secrets
from typing import Any
import aiofiles
import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.models import Track, UploadJob
from app.services.cache import cache
from app.services.jiosaavn import JioSaavnProvider
from app.services.yukysbox import YukisboxStorage
from app.utils import sanitize_slug

logger = logging.getLogger(__name__)

class MusicService:
    def __init__(self):
        self.provider = JioSaavnProvider()
        self.storage = YukisboxStorage()
        self._inflight_locks: dict[str, asyncio.Lock] = {}
        self._inflight_global_lock = asyncio.Lock()
        os.makedirs(settings.TEMP_DIR, exist_ok=True)

    async def _get_track_lock(self, track_id: str) -> asyncio.Lock:
        async with self._inflight_global_lock:
            if track_id not in self._inflight_locks:
                self._inflight_locks[track_id] = asyncio.Lock()
            return self._inflight_locks[track_id]

    async def search(self, query: str, limit: int = 10, db: AsyncSession | None = None) -> list[dict[str, Any]]:
        """Search tracks with cache and provider fallback."""
        cache_key = f"search:{query.strip().lower()}:{limit}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        results = await self.provider.search(query, limit=limit)
        if results:
            cache.set(cache_key, results, ttl=1800)  # 30 min cache
        return results

    async def get_track(self, track_id: str, db: AsyncSession) -> dict[str, Any] | None:
        """Fetch track metadata from DB or provider."""
        # 1. DB lookup
        res = await db.execute(select(Track).where(Track.track_id == track_id))
        track = res.scalar_one_or_none()
        if track:
            return {
                "track_id": track.track_id,
                "title": track.title,
                "artist": track.artist,
                "album": track.album,
                "duration": track.duration,
                "language": track.language,
                "thumbnail": track.thumbnail,
                "storage_provider": track.storage_provider,
                "storage_uid": track.storage_uid,
                "storage_url": track.storage_url,
                "status": track.status
            }

        # 2. Provider lookup
        provider_track = await self.provider.get_track(track_id)
        if provider_track:
            return provider_track

        return None

    async def resolve(self, track_id: str, db: AsyncSession) -> dict[str, Any]:
        """
        Acquire, cache, and deliver storage URL for a track.
        Guarantees single-flight upload and database deduplication.
        """
        cache_key = f"resolve:{track_id}"
        cached_url = cache.get(cache_key)
        if cached_url:
            res = await db.execute(select(Track).where(Track.track_id == track_id))
            track = res.scalar_one_or_none()
            if track and track.storage_url and track.status == "available":
                return {
                    "track_id": track.track_id,
                    "title": track.title,
                    "artist": track.artist,
                    "storage": track.storage_provider,
                    "storage_uid": track.storage_uid,
                    "music_url": track.storage_url,
                    "cached": True,
                    "duration": track.duration
                }

        # 1. DB Check
        res = await db.execute(select(Track).where(Track.track_id == track_id))
        track = res.scalar_one_or_none()
        if track and track.storage_url and track.status == "available":
            cache.set(cache_key, track.storage_url, ttl=7200)
            return {
                "track_id": track.track_id,
                "title": track.title,
                "artist": track.artist,
                "storage": track.storage_provider,
                "storage_uid": track.storage_uid,
                "music_url": track.storage_url,
                "cached": True,
                "duration": track.duration
            }

        # 2. In-Flight Single-Flight Lock
        track_lock = await self._get_track_lock(track_id)
        async with track_lock:
            # Re-check DB in case concurrent flight completed upload
            res = await db.execute(select(Track).where(Track.track_id == track_id))
            track = res.scalar_one_or_none()
            if track and track.storage_url and track.status == "available":
                cache.set(cache_key, track.storage_url, ttl=7200)
                return {
                    "track_id": track.track_id,
                    "title": track.title,
                    "artist": track.artist,
                    "storage": track.storage_provider,
                    "storage_uid": track.storage_uid,
                    "music_url": track.storage_url,
                    "cached": True,
                    "duration": track.duration
                }

            # 3. Obtain track metadata and source URL from provider
            p_data = await self.provider.get_track(track_id)
            if not p_data or not p_data.get("source_url"):
                raise ValueError(f"Unable to find track or authorized audio source for {track_id}")

            source_url = p_data["source_url"]
            target_slug = sanitize_slug(f"{p_data['title']}_{p_data['artist']}_{p_data['provider_song_id']}")

            # 4. Upload to Yukisbox (Method A with Method B fallback)
            storage_res = None
            try:
                logger.info("Attempting Yukisbox Method A (Remote URL) for %s...", track_id)
                storage_res = await self.storage.upload_url(source_url, custom_slug=target_slug)
            except Exception as e:
                logger.warning("Method A failed (%s). Attempting Method B (Streamed file upload)...", e)

            if not storage_res or not storage_res.get("success"):
                # Method B Fallback
                temp_file = os.path.join(
                    settings.TEMP_DIR,
                    f"temp_{track_id}_{secrets.token_hex(4)}.{p_data.get('audio_format', 'mp3')}"
                )
                try:
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        async with client.stream("GET", source_url) as resp:
                            resp.raise_for_status()
                            async with aiofiles.open(temp_file, "wb") as f:
                                async for chunk in resp.aiter_bytes(chunk_size=65536):
                                    await f.write(chunk)

                    storage_res = await self.storage.upload_file(temp_file, custom_slug=target_slug)
                finally:
                    if os.path.exists(temp_file):
                        try:
                            os.remove(temp_file)
                        except Exception:
                            pass

            if not storage_res or not storage_res.get("success"):
                raise RuntimeError(f"Storage upload failed for track {track_id}")

            # 5. Persist to PostgreSQL
            if not track:
                track = Track(
                    track_id=track_id,
                    provider=p_data.get("provider", "jiosaavn"),
                    provider_song_id=p_data.get("provider_song_id", track_id),
                    title=p_data.get("title", "Unknown Title"),
                    artist=p_data.get("artist", "Unknown Artist"),
                    album=p_data.get("album"),
                    duration=p_data.get("duration", 0),
                    language=p_data.get("language"),
                    thumbnail=p_data.get("thumbnail"),
                    source_url=source_url,
                    storage_provider="yukisbox",
                    storage_uid=storage_res["uid"],
                    storage_url=storage_res["url"],
                    file_size=storage_res.get("file_size"),
                    audio_format=p_data.get("audio_format", "mp3"),
                    status="available",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                    last_accessed_at=datetime.utcnow()
                )
                db.add(track)
            else:
                track.storage_uid = storage_res["uid"]
                track.storage_url = storage_res["url"]
                track.status = "available"
                track.file_size = storage_res.get("file_size")
                track.updated_at = datetime.utcnow()
                track.last_accessed_at = datetime.utcnow()

            await db.commit()
            cache.set(cache_key, track.storage_url, ttl=7200)

            return {
                "track_id": track.track_id,
                "title": track.title,
                "artist": track.artist,
                "storage": track.storage_provider,
                "storage_uid": track.storage_uid,
                "music_url": track.storage_url,
                "cached": False,
                "duration": track.duration
            }

music_service = MusicService()

