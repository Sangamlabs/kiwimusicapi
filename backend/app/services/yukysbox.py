import asyncio
import ipaddress
import logging
import os
from typing import Any
from urllib.parse import urlparse
import aiofiles
import httpx
from app.config import settings
from app.services.storage import StorageBackend
from app.utils import sanitize_slug

logger = logging.getLogger(__name__)

class YukisboxStorage(StorageBackend):
    """
    Concrete storage adapter for Yukisbox (https://yukiapi.site).
    Implements Method A (Remote URL upload) and Method B (Streamed file upload fallback).
    """

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or settings.YUKISBOX_BASE_URL or "https://yukiapi.site").rstrip("/")
        self.timeout = timeout or float(settings.YUKISBOX_TIMEOUT)
        self.semaphore = asyncio.Semaphore(settings.MAX_UPLOAD_CONCURRENCY)

    def _is_safe_url(self, url: str) -> bool:
        """Validate URL scheme and ensure destination is not a private IP (SSRF guard)."""
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return False
            hostname = parsed.hostname or ""
            if not hostname:
                return False
            # Check for private or loopback IP addresses
            try:
                ip = ipaddress.ip_address(hostname)
                if ip.is_private or ip.is_loopback or ip.is_reserved:
                    return False
            except ValueError:
                # Not a raw IP, domain name is acceptable
                pass
            return True
        except Exception:
            return False

    async def get_file_info(self, uid: str) -> dict[str, Any] | None:
        """Fetch metadata for a file from /v1/file/{uid}/info."""
        if not uid:
            return None
        clean_uid = sanitize_slug(uid)
        url = f"{self.base_url}/v1/file/{clean_uid}/info"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and data.get("uid"):
                        return data
                elif resp.status_code == 404:
                    return None
        except Exception as e:
            logger.warning("Error querying Yukisbox file info for %s: %s", uid, e)
        return None

    async def validate_url(self, uid: str) -> bool:
        """Verify that a file is available and supports HTTP Range streaming."""
        info = await self.get_file_info(uid)
        if not info or info.get("size", 0) <= 0:
            return False

        file_url = f"{self.base_url}/file/{uid}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                headers = {"Range": "bytes=0-1023"}
                resp = await client.get(file_url, headers=headers)
                return resp.status_code in (200, 206)
        except Exception as e:
            logger.warning("Yukisbox range validation failed for %s: %s", uid, e)
            return False

    async def upload_url(self, source_url: str, custom_slug: str | None = None) -> dict[str, Any]:
        """
        Method A: Remote URL upload to Yukisbox via POST /v1/upload-url.
        Uses application/x-www-form-urlencoded as required by Yukisbox OpenAPI.
        """
        if not self._is_safe_url(source_url):
            raise ValueError(f"Unsafe or invalid source URL rejected: {source_url}")

        clean_slug = sanitize_slug(custom_slug) if custom_slug else None

        # 1. Pre-upload existence check
        if clean_slug:
            existing = await self.get_file_info(clean_slug)
            if existing and existing.get("size", 0) > 0:
                logger.info("Slug '%s' already exists on Yukisbox. Reusing.", clean_slug)
                return {
                    "success": True,
                    "uid": existing["uid"],
                    "url": f"{self.base_url}/file/{existing['uid']}",
                    "storage_provider": "yukisbox",
                    "reused": True,
                    "file_size": existing.get("size")
                }

        async with self.semaphore:
            target_slug = clean_slug
            attempt = 0
            max_collision_attempts = 3

            while attempt < max_collision_attempts:
                data = {"url": source_url}
                if target_slug:
                    data["custom_slug"] = target_slug

                try:
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        resp = await client.post(
                            f"{self.base_url}/v1/upload-url",
                            data=data,
                            headers={"Content-Type": "application/x-www-form-urlencoded"}
                        )

                        if resp.status_code == 200:
                            res_json = resp.json()
                            if res_json.get("success"):
                                return {
                                    "success": True,
                                    "uid": res_json["uid"],
                                    "url": res_json.get("url") or f"{self.base_url}/file/{res_json['uid']}",
                                    "storage_provider": "yukisbox",
                                    "reused": False,
                                    "file_size": None
                                }

                        # Handle slug collision
                        if resp.status_code == 400 and "already taken" in resp.text:
                            attempt += 1
                            target_slug = f"{clean_slug}-v{attempt + 1}"
                            logger.info("Slug taken on upload-url, retrying with '%s'", target_slug)
                            continue

                        # If remote URL upload fails with non-recoverable error
                        logger.warning("Yukisbox upload-url failed: %d -> %s", resp.status_code, resp.text)
                        break

                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    logger.warning("Yukisbox upload-url network failure: %s", e)
                    break

        raise RuntimeError("Yukisbox Method A (Remote URL) upload failed")

    async def upload_file(self, file_path: str, custom_slug: str | None = None) -> dict[str, Any]:
        """
        Method B: Local file upload fallback via POST /v1/upload (multipart).
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_size = os.path.getsize(file_path)
        max_bytes = settings.MAX_DOWNLOAD_SIZE_MB * 1024 * 1024
        if file_size > max_bytes:
            raise ValueError(f"File size {file_size} exceeds maximum {max_bytes} bytes")

        clean_slug = sanitize_slug(custom_slug) if custom_slug else None

        # Pre-upload existence check
        if clean_slug:
            existing = await self.get_file_info(clean_slug)
            if existing and existing.get("size", 0) > 0:
                logger.info("Slug '%s' already exists on Yukisbox. Reusing.", clean_slug)
                return {
                    "success": True,
                    "uid": existing["uid"],
                    "url": f"{self.base_url}/file/{existing['uid']}",
                    "storage_provider": "yukisbox",
                    "reused": True,
                    "file_size": existing.get("size")
                }

        async with self.semaphore:
            target_slug = clean_slug
            attempt = 0
            max_collision_attempts = 3

            while attempt < max_collision_attempts:
                filename = os.path.basename(file_path)
                data = {}
                if target_slug:
                    data["custom_slug"] = target_slug

                try:
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        # Stream file from disk
                        async with aiofiles.open(file_path, "rb") as f:
                            content = await f.read()

                        files = {"file": (filename, content, "audio/mpeg")}
                        resp = await client.post(
                            f"{self.base_url}/v1/upload",
                            files=files,
                            data=data
                        )

                        if resp.status_code == 200:
                            res_json = resp.json()
                            if res_json.get("success"):
                                return {
                                    "success": True,
                                    "uid": res_json["uid"],
                                    "url": res_json.get("url") or f"{self.base_url}/file/{res_json['uid']}",
                                    "storage_provider": "yukisbox",
                                    "reused": False,
                                    "file_size": file_size
                                }

                        if resp.status_code == 400 and "already taken" in resp.text:
                            attempt += 1
                            target_slug = f"{clean_slug}-v{attempt + 1}"
                            continue

                        logger.warning("Yukisbox upload failed: %d -> %s", resp.status_code, resp.text)
                        break

                except Exception as e:
                    logger.error("Yukisbox upload error: %s", e)
                    break

        raise RuntimeError("Yukisbox Method B (Local upload) failed")

