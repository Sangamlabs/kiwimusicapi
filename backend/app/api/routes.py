import time
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
from app.api.auth import consume_quota_atomic, get_api_key, record_failed_request
from app.database import get_db
from app.models import ApiKey
from app.schemas import (
    HealthResponse,
    ResolveResponse,
    SearchResponse,
    TrackDetailResponse,
    TrackSearchResult,
    UsageResponse
)
from app.services.music import music_service

router = APIRouter(prefix="/v1", tags=["Music API"])

@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)):
    """Health check for API, Database, Storage, and Provider."""
    # Check DB
    db_status = "operational"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "degraded"

    # Check Storage
    storage_status = "operational"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("https://yukiapi.site/v1/server/stats")
            if r.status_code != 200:
                storage_status = "unreachable"
    except Exception:
        storage_status = "unreachable"

    return HealthResponse(
        success=True,
        status="healthy" if db_status == "operational" else "degraded",
        version="1.0.0",
        database=db_status,
        storage=storage_status,
        jiosaavn="operational"
    )

@router.get("/search", response_model=SearchResponse)
async def search_endpoint(
    q: str = Query(..., min_length=1, description="Song title, artist, or query"),
    limit: int = Query(10, ge=1, le=50),
    api_key: ApiKey = Depends(get_api_key)
):
    """Search for music tracks. (Free endpoint, does not consume quota)."""
    results = await music_service.search(q, limit=limit)
    items = [
        TrackSearchResult(
            track_id=r["track_id"],
            title=r["title"],
            artist=r["artist"],
            album=r.get("album"),
            duration=r.get("duration", 0),
            thumbnail=r.get("thumbnail")
        )
        for r in results
    ]
    return SearchResponse(success=True, count=len(items), results=items)

@router.get("/track/{track_id}", response_model=TrackDetailResponse)
async def track_detail_endpoint(
    track_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Retrieve track details. (Free endpoint, does not consume quota)."""
    track = await music_service.get_track(track_id, db)
    if not track:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TRACK_NOT_FOUND", "message": f"Track {track_id} not found"}
        )

    return TrackDetailResponse(
        success=True,
        track_id=track["track_id"],
        title=track["title"],
        artist=track["artist"],
        album=track.get("album"),
        duration=track.get("duration", 0),
        language=track.get("language"),
        thumbnail=track.get("thumbnail"),
        storage_provider=track.get("storage_provider", "yukisbox"),
        storage_uid=track.get("storage_uid"),
        storage_url=track.get("storage_url"),
        status=track.get("status", "available")
    )

@router.get("/resolve/{track_id}", response_model=ResolveResponse)
async def resolve_endpoint(
    track_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Resolve track to Yukisbox storage URL.
    Billable endpoint: Consumes 1 quota unit upon success. Failed attempts do NOT consume quota.
    """
    start_time = time.time()
    try:
        data = await music_service.resolve(track_id, db)
        latency_ms = (time.time() - start_time) * 1000

        # Atomically consume quota for successful resolution
        quota_ok = await consume_quota_atomic(db, api_key.id, "/v1/resolve", latency_ms)
        if not quota_ok:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "QUOTA_EXHAUSTED", "message": "API key quota has been exhausted"}
            )

        return ResolveResponse(
            success=True,
            track_id=data["track_id"],
            title=data["title"],
            artist=data["artist"],
            storage=data["storage"],
            storage_uid=data["storage_uid"],
            music_url=data["music_url"],
            cached=data["cached"],
            duration=data["duration"]
        )

    except HTTPException:
        raise
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        await record_failed_request(db, api_key.id, "/v1/resolve", 500, latency_ms)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "RESOLUTION_FAILED", "message": str(e)}
        )

@router.get("/stream/{track_id}")
async def stream_endpoint(
    track_id: str,
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Redirect (302) to direct Yukisbox audio streaming URL with HTTP Range support.
    Billable endpoint: Consumes 1 quota unit upon success.
    """
    start_time = time.time()
    try:
        data = await music_service.resolve(track_id, db)
        latency_ms = (time.time() - start_time) * 1000

        quota_ok = await consume_quota_atomic(db, api_key.id, "/v1/stream", latency_ms)
        if not quota_ok:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "QUOTA_EXHAUSTED", "message": "API key quota has been exhausted"}
            )

        return RedirectResponse(url=data["music_url"], status_code=status.HTTP_302_FOUND)

    except HTTPException:
        raise
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        await record_failed_request(db, api_key.id, "/v1/stream", 500, latency_ms)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "STREAM_FAILED", "message": str(e)}
        )

@router.get("/usage", response_model=UsageResponse)
async def usage_endpoint(
    api_key: ApiKey = Depends(get_api_key)
):
    """Retrieve usage stats for the calling API key. (Free endpoint, does not consume quota)."""
    return UsageResponse(
        success=True,
        key_prefix=api_key.key_prefix,
        total_quota=api_key.total_quota,
        used_requests=api_key.used_requests,
        failed_requests=api_key.failed_requests,
        remaining_requests=max(0, api_key.total_quota - api_key.used_requests),
        status=api_key.status
    )

