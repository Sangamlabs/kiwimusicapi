from abc import ABC, abstractmethod
from typing import Any

class MusicProvider(ABC):
    """Abstract interface for music providers."""

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search tracks by query string."""
        pass

    @abstractmethod
    async def get_track(self, track_id: str) -> dict[str, Any] | None:
        """Fetch full metadata for a track by provider song ID."""
        pass

    @abstractmethod
    async def get_audio_source(self, track_id: str) -> dict[str, Any] | None:
        """Obtain authorized audio stream URL and format for a track."""
        pass

