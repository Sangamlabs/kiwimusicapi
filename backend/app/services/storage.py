from abc import ABC, abstractmethod
from typing import Any

class StorageBackend(ABC):
    """Abstract interface for storage backends."""

    @abstractmethod
    async def upload_url(self, source_url: str, custom_slug: str | None = None) -> dict[str, Any]:
        """Upload a remote audio URL directly to storage."""
        pass

    @abstractmethod
    async def upload_file(self, file_path: str, custom_slug: str | None = None) -> dict[str, Any]:
        """Upload a local audio file to storage."""
        pass

    @abstractmethod
    async def get_file_info(self, uid: str) -> dict[str, Any] | None:
        """Fetch metadata for a stored file by UID / slug."""
        pass

    @abstractmethod
    async def validate_url(self, uid: str) -> bool:
        """Validate that a stored file exists and is streamable."""
        pass

