import logging
from typing import Any
import httpx
from config import bot_settings

logger = logging.getLogger(__name__)

class BackendClient:
    """Async HTTP client communicating securely with Kiwi Music API backend."""

    def __init__(self, base_url: str | None = None, secret: str | None = None, timeout: float = 15.0):
        self.base_url = (base_url or bot_settings.BACKEND_API_URL).rstrip("/")
        self.secret = secret or bot_settings.BACKEND_INTERNAL_SECRET
        self.timeout = timeout
        self.headers = {
            "X-Internal-Secret": self.secret,
            "Accept": "application/json"
        }

    async def generate_key(self, telegram_user_id: int) -> dict[str, Any]:
        """Request backend to generate a new 200-quota API key."""
        url = f"{self.base_url}/internal/api-keys/generate"
        payload = {"telegram_user_id": telegram_user_id}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def list_keys(self, telegram_user_id: int) -> list[dict[str, Any]]:
        """Fetch list of all API keys owned by a Telegram user."""
        url = f"{self.base_url}/internal/api-keys/list"
        params = {"telegram_user_id": telegram_user_id}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params, headers=self.headers)
            resp.raise_for_status()
            return resp.json().get("keys", [])

    async def get_usage(self, telegram_user_id: int, key_prefix: str | None = None) -> dict[str, Any]:
        """Fetch usage stats for user's latest or specific key."""
        url = f"{self.base_url}/internal/api-keys/usage"
        params: dict[str, Any] = {"telegram_user_id": telegram_user_id}
        if key_prefix:
            params["key_prefix"] = key_prefix
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def revoke_key(self, telegram_user_id: int, key_prefix: str) -> dict[str, Any]:
        """Revoke a user's API key."""
        url = f"{self.base_url}/internal/api-keys/revoke"
        payload = {"telegram_user_id": telegram_user_id, "key_prefix": key_prefix}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_stats(self) -> dict[str, Any]:
        """Fetch system-wide owner operational statistics."""
        url = f"{self.base_url}/internal/stats"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_health(self) -> dict[str, Any]:
        """Check backend health status."""
        url = f"{self.base_url}/v1/health"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()

backend_client = BackendClient()

