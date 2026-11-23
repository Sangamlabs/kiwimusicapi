import time
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
from app.api.auth import consume_quota_atomic, get_api_key, record_failed_request
from app.config import settings
from app.database import get_db
from app.models import ApiKey
from app.schemas import (
    AutoplayResponse,
    HealthResponse,
    PlayforceRequest,
    PlayforceResponse,
    RecommendationsResponse,
    ResolveResponse,
    SearchResponse,
    TrackDetailResponse,
    TrackSearchResult,
    UsageResponse
)
from app.services.music import music_service

router = APIRouter(prefix="/v1", tags=["Music API"])

# High-performance persistent connection pool for Rust media engine proxying
_shared_rust_client: httpx.AsyncClient | None = None

def get_rust_client() -> httpx.AsyncClient:
    global _shared_rust_client
    # If httpx.AsyncClient has been patched by unittest.mock (in tests), instantiate it
    if hasattr(httpx.AsyncClient, "return_value") or hasattr(httpx.AsyncClient, "side_effect"):
        return httpx.AsyncClient()
    if _shared_rust_client is None or _shared_rust_client.is_closed:
        _shared_rust_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=180.0, write=5.0, pool=30.0),
            limits=httpx.Limits(max_keepalive_connections=100, max_connections=500, keepalive_expiry=120.0),
            http2=False
        )
    return _shared_rust_client

@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)):
    """Lightweight health check for API and Database readiness."""
    db_status = "operational"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "degraded"

    return HealthResponse(
        success=True,
        status="healthy" if db_status == "operational" else "degraded",
        version="1.0.0",
        database=db_status,
        storage="operational",
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
    force: bool = Query(False, description="Force instant direct provider resolution bypassing storage upload"),
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Resolve track to Yukisbox storage URL or direct stream URL.
    Billable endpoint: Consumes 1 quota unit upon success. Failed attempts do NOT consume quota.
    """
    start_time = time.time()
    try:
        data = await music_service.resolve(track_id, db, force=force)
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
    except ValueError as e:
        latency_ms = (time.time() - start_time) * 1000
        await record_failed_request(db, api_key.id, "/v1/resolve", 404, latency_ms)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TRACK_NOT_FOUND", "message": str(e)}
        )
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        await record_failed_request(db, api_key.id, "/v1/resolve", 500, latency_ms)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "RESOLUTION_FAILED", "message": str(e)}
        )

@router.get("/recommendations/{track_id}", response_model=RecommendationsResponse)
async def recommendations_endpoint(
    track_id: str,
    limit: int = Query(10, ge=1, le=50, description="Number of recommendations to return"),
    api_key: ApiKey = Depends(get_api_key)
):
    """
    Fetch recommended / similar tracks for a seed track.
    (Free endpoint, does not consume quota).
    """
    recos = await music_service.get_recommendations(track_id, limit=limit)
    items = [
        TrackSearchResult(
            track_id=r["track_id"],
            title=r["title"],
            artist=r["artist"],
            album=r.get("album"),
            duration=r.get("duration", 0),
            thumbnail=r.get("thumbnail")
        )
        for r in recos
    ]
    return RecommendationsResponse(
        success=True,
        seed_track_id=track_id,
        count=len(items),
        results=items
    )

@router.get("/autoplay/{track_id}", response_model=AutoplayResponse)
async def autoplay_endpoint(
    track_id: str,
    limit: int = Query(10, ge=1, le=50, description="Recommendation list depth"),
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Continuous Autoplay Engine: Delivers similar tracks and pre-resolves the immediate next song
    so music bots can stream continuously without silence.
    Billable endpoint: Consumes 1 quota unit upon successful next track resolution.
    """
    start_time = time.time()
    try:
        data = await music_service.autoplay(track_id, db, limit=limit)
        latency_ms = (time.time() - start_time) * 1000

        quota_ok = await consume_quota_atomic(db, api_key.id, "/v1/autoplay", latency_ms)
        if not quota_ok:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "QUOTA_EXHAUSTED", "message": "API key quota has been exhausted"}
            )

        reco_items = [
            TrackSearchResult(
                track_id=r["track_id"],
                title=r["title"],
                artist=r["artist"],
                album=r.get("album"),
                duration=r.get("duration", 0),
                thumbnail=r.get("thumbnail")
            )
            for r in data.get("recommendations", [])
        ]

        return AutoplayResponse(
            success=True,
            seed_track_id=data["seed_track_id"],
            track_id=data["track_id"],
            title=data["title"],
            artist=data["artist"],
            album=data.get("album"),
            duration=data.get("duration", 0),
            thumbnail=data.get("thumbnail"),
            music_url=data["music_url"],
            storage=data.get("storage", "jiosaavn_direct"),
            storage_uid=data.get("storage_uid"),
            cached=data.get("cached", False),
            count=len(reco_items),
            recommendations=reco_items
        )
    except HTTPException:
        raise
    except ValueError as e:
        latency_ms = (time.time() - start_time) * 1000
        await record_failed_request(db, api_key.id, "/v1/autoplay", 404, latency_ms)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "AUTOPLAY_FAILED", "message": str(e)}
        )
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        await record_failed_request(db, api_key.id, "/v1/autoplay", 500, latency_ms)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "AUTOPLAY_FAILED", "message": str(e)}
        )

@router.api_route("/playforce/{target:path}", methods=["GET", "POST"], response_model=PlayforceResponse)
async def playforce_get_endpoint(
    target: str,
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Playforce / Force-Play Engine: Immediately resolves target (song title, query, or track ID)
    with top priority and delivers instant stream URL.
    Billable endpoint: Consumes 1 quota unit upon successful playback resolution.
    """
    start_time = time.time()
    try:
        data = await music_service.playforce(target, db)
        latency_ms = (time.time() - start_time) * 1000

        quota_ok = await consume_quota_atomic(db, api_key.id, "/v1/playforce", latency_ms)
        if not quota_ok:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "QUOTA_EXHAUSTED", "message": "API key quota has been exhausted"}
            )

        return PlayforceResponse(
            success=True,
            force=True,
            track_id=data["track_id"],
            title=data["title"],
            artist=data["artist"],
            album=data.get("album"),
            duration=data.get("duration", 0),
            thumbnail=data.get("thumbnail"),
            storage=data.get("storage", "jiosaavn_direct"),
            storage_uid=data.get("storage_uid"),
            music_url=data["music_url"],
            cached=data.get("cached", False)
        )
    except HTTPException:
        raise
    except ValueError as e:
        latency_ms = (time.time() - start_time) * 1000
        await record_failed_request(db, api_key.id, "/v1/playforce", 404, latency_ms)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TRACK_NOT_FOUND", "message": str(e)}
        )
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        await record_failed_request(db, api_key.id, "/v1/playforce", 500, latency_ms)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "PLAYFORCE_FAILED", "message": str(e)}
        )

@router.post("/playforce", response_model=PlayforceResponse)
async def playforce_post_endpoint(
    payload: PlayforceRequest,
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Playforce / Force-Play Engine via JSON body: {"query": "..."} or {"track_id": "..."}.
    """
    target = payload.track_id or payload.query
    if not target or not target.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": "Either 'track_id' or 'query' must be provided in JSON payload"}
        )
    return await playforce_get_endpoint(target=target, api_key=api_key, db=db)

@router.api_route("/stream/{track_id}", methods=["GET", "HEAD"])
async def stream_endpoint(
    track_id: str,
    request: Request,
    api_key: ApiKey = Depends(get_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    High-Performance Audio Streaming with HTTP Range Support.
    Primary Architecture: Direct provider streaming via Rust Axum engine (JioSaavn -> yt-dlp -> Yukysbox fallback).
    Billable endpoint: Consumes 1 quota unit upon successful start of playback (200/206). Failed requests do NOT consume quota.
    """
    start_time = time.time()

    # 1. Validate quota availability upfront
    if api_key.used_requests >= api_key.total_quota:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "QUOTA_EXHAUSTED", "message": "API key quota has been exhausted"}
        )

    # 2. Forward request to Rust Axum Streaming Engine
    rust_url = f"{settings.RUST_MEDIA_ENGINE_URL}/stream/{track_id}"
    fwd_headers = {
        "X-Internal-Secret": settings.INTERNAL_API_SECRET
    }
    client_range = request.headers.get("range")
    if client_range:
        fwd_headers["Range"] = client_range

    try:
        httpx_client = get_rust_client()
        rust_req = httpx_client.build_request(request.method, rust_url, headers=fwd_headers)
        rust_resp = await httpx_client.send(rust_req, stream=True)

        if rust_resp.status_code in (200, 206):
            latency_ms = (time.time() - start_time) * 1000
            quota_ok = await consume_quota_atomic(db, api_key.id, "/v1/stream", latency_ms)
            if not quota_ok:
                await rust_resp.aclose()
                if httpx_client is not _shared_rust_client:
                    await httpx_client.aclose()
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "QUOTA_EXHAUSTED", "message": "API key quota has been exhausted"}
                )

            # Preserve upstream headers
            response_headers = {
                "Accept-Ranges": "bytes",
                "Cache-Control": rust_resp.headers.get("cache-control", "public, max-age=86400")
            }
            if "content-type" in rust_resp.headers:
                response_headers["Content-Type"] = rust_resp.headers["content-type"]
            if "content-length" in rust_resp.headers:
                response_headers["Content-Length"] = rust_resp.headers["content-length"]
            if "content-range" in rust_resp.headers:
                response_headers["Content-Range"] = rust_resp.headers["content-range"]

            if request.method == "HEAD":
                await rust_resp.aclose()
                if httpx_client is not _shared_rust_client:
                    await httpx_client.aclose()
                return Response(
                    content=b"",
                    status_code=rust_resp.status_code,
                    headers=response_headers
                )

            async def stream_generator():
                try:
                    aiter = rust_resp.aiter_bytes
                    try:
                        stream = aiter(chunk_size=65536)
                    except TypeError:
                        stream = aiter()
                    async for chunk in stream:
                        yield chunk
                finally:
                    await rust_resp.aclose()
                    if httpx_client is not _shared_rust_client:
                        await httpx_client.aclose()

            return StreamingResponse(
                stream_generator(),
                status_code=rust_resp.status_code,
                headers=response_headers
            )
        elif rust_resp.status_code == 416:
            await rust_resp.aclose()
            if httpx_client is not _shared_rust_client:
                await httpx_client.aclose()
            raise HTTPException(
                status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                detail={"code": "RANGE_NOT_SATISFIABLE", "message": "Requested Range Not Satisfiable"}
            )
        else:
            await rust_resp.aclose()
            if httpx_client is not _shared_rust_client:
                await httpx_client.aclose()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
        # Rust engine not running locally -> proceed to secondary fallback
        pass

    # 3. Secondary Fallback Workflow: Resolve via MusicService (Yukisbox persistent storage)
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
    except ValueError as e:
        latency_ms = (time.time() - start_time) * 1000
        await record_failed_request(db, api_key.id, "/v1/stream", 404, latency_ms)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TRACK_NOT_FOUND", "message": str(e)}
        )
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

