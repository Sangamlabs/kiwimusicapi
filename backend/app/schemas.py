from datetime import datetime
from pydantic import BaseModel, Field

# --- Public API Schemas ---

class TrackSearchResult(BaseModel):
    track_id: str
    title: str
    artist: str
    album: str | None = None
    duration: int
    thumbnail: str | None = None

class SearchResponse(BaseModel):
    success: bool = True
    count: int
    results: list[TrackSearchResult]

class TrackDetailResponse(BaseModel):
    success: bool = True
    track_id: str
    title: str
    artist: str
    album: str | None = None
    duration: int
    language: str | None = None
    thumbnail: str | None = None
    storage_provider: str = "yukisbox"
    storage_uid: str | None = None
    storage_url: str | None = None
    status: str

class ResolveResponse(BaseModel):
    success: bool = True
    track_id: str
    title: str
    artist: str
    storage: str = "yukisbox"
    storage_uid: str
    music_url: str
    cached: bool
    duration: int

class RecommendationsResponse(BaseModel):
    success: bool = True
    seed_track_id: str
    count: int
    results: list[TrackSearchResult]

class AutoplayResponse(BaseModel):
    success: bool = True
    seed_track_id: str
    track_id: str
    title: str
    artist: str
    album: str | None = None
    duration: int
    thumbnail: str | None = None
    music_url: str
    storage: str = "jiosaavn_direct"
    storage_uid: str | None = None
    cached: bool = False
    count: int = 0
    recommendations: list[TrackSearchResult] = Field(default_factory=list)

class PlayforceRequest(BaseModel):
    query: str | None = None
    track_id: str | None = None

class PlayforceResponse(BaseModel):
    success: bool = True
    force: bool = True
    track_id: str
    title: str
    artist: str
    album: str | None = None
    duration: int
    thumbnail: str | None = None
    storage: str = "jiosaavn_direct"
    storage_uid: str | None = None
    music_url: str
    cached: bool = False

class UsageResponse(BaseModel):
    success: bool = True
    key_prefix: str
    total_quota: int
    used_requests: int
    failed_requests: int
    remaining_requests: int
    status: str

class HealthResponse(BaseModel):
    success: bool = True
    status: str
    version: str = "1.0.0"
    database: str
    storage: str
    jiosaavn: str

class ErrorDetail(BaseModel):
    code: str
    message: str

class GenericErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail

# --- Internal Management Schemas ---

class KeyGenerateRequest(BaseModel):
    telegram_user_id: int

class KeyGenerateResponse(BaseModel):
    success: bool = True
    already_exists: bool = False
    api_key: str | None = None
    key_prefix: str
    quota_limit: int = 200
    used_requests: int = 0
    remaining_requests: int = 200
    status: str = "active"
    message: str = "Store this API key securely. It will not be shown again."

class KeyItem(BaseModel):
    key_prefix: str
    api_key: str | None = None
    total_quota: int
    used_requests: int
    remaining_requests: int
    status: str
    created_at: datetime

class KeyListResponse(BaseModel):
    success: bool = True
    keys: list[KeyItem]

class KeyRevokeRequest(BaseModel):
    telegram_user_id: int
    key_prefix: str

class KeyRevokeResponse(BaseModel):
    success: bool = True
    message: str

class KeyUsageResponse(BaseModel):
    success: bool = True
    key_prefix: str
    total_quota: int
    used_requests: int
    failed_requests: int
    remaining_requests: int
    status: str

class InternalStatsResponse(BaseModel):
    success: bool = True
    total_users: int
    total_keys: int
    active_keys: int
    total_tracks: int
    total_usage_events: int

