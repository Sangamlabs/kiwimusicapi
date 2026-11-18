from datetime import datetime
from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.database import get_db
from app.models import ApiKey, ApiUsage
from app.utils import hash_api_key

async def get_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    kiwiapi_header: str | None = Header(None, alias="KIWIAPI"),
    authorization: str | None = Header(None, alias="Authorization"),
    api_key_query: str | None = Query(None, alias="api_key"),
    kiwiapi_query: str | None = Query(None, alias="kiwiapi"),
    db: AsyncSession = Depends(get_db)
) -> ApiKey:
    """
    Validate API key from header or query param against database and quota limits.
    Supports:
      - Header 'X-API-Key'
      - Header 'KIWIAPI' (direct environment variable name match)
      - Header 'Authorization: Bearer <key>'
      - Query param '?api_key=<key>' or '?kiwiapi=<key>'
    """
    raw_key = x_api_key or kiwiapi_header or api_key_query or kiwiapi_query
    if not raw_key and authorization:
        if authorization.lower().startswith("bearer "):
            raw_key = authorization[7:].strip()
        else:
            raw_key = authorization.strip()

    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "MISSING_API_KEY", "message": "API key required via X-API-Key, KIWIAPI header, or Authorization Bearer"}
        )

    key_hash = hash_api_key(raw_key.strip())
    result = await db.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash)
    )
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_API_KEY", "message": "The provided API key does not exist"}
        )

    if api_key.status == "revoked":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "KEY_REVOKED", "message": "This API key has been revoked"}
        )

    if api_key.status != "active" or api_key.used_requests >= api_key.total_quota:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "QUOTA_EXHAUSTED",
                "message": f"Lifetime quota of {api_key.total_quota} requests has been exhausted"
            }
        )

    return api_key

async def consume_quota_atomic(
    db: AsyncSession,
    api_key_id: int,
    endpoint: str,
    latency_ms: float | None = None
) -> bool:
    """
    Atomically increment used_requests in a single SQL statement.
    Only succeeds if used_requests < total_quota and status == 'active'.
    Logs success to api_usage.
    """
    stmt = (
        update(ApiKey)
        .where(
            ApiKey.id == api_key_id,
            ApiKey.used_requests < ApiKey.total_quota,
            ApiKey.status == "active"
        )
        .values(used_requests=ApiKey.used_requests + 1)
        .returning(ApiKey.used_requests, ApiKey.total_quota)
    )
    res = await db.execute(stmt)
    row = res.fetchone()

    if not row:
        return False

    used, total = row
    if used >= total:
        await db.execute(
            update(ApiKey)
            .where(ApiKey.id == api_key_id)
            .values(status="exhausted")
        )

    # Log usage event
    usage_entry = ApiUsage(
        api_key_id=api_key_id,
        endpoint=endpoint,
        status_code=200,
        success=True,
        latency_ms=latency_ms,
        created_at=datetime.utcnow()
    )
    db.add(usage_entry)
    await db.commit()
    return True

async def record_failed_request(
    db: AsyncSession,
    api_key_id: int,
    endpoint: str,
    status_code: int,
    latency_ms: float | None = None
) -> None:
    """
    Record a failed request without consuming lifetime quota.
    Updates failed_requests counter and logs to api_usage.
    """
    stmt = (
        update(ApiKey)
        .where(ApiKey.id == api_key_id)
        .values(failed_requests=ApiKey.failed_requests + 1)
    )
    await db.execute(stmt)

    usage_entry = ApiUsage(
        api_key_id=api_key_id,
        endpoint=endpoint,
        status_code=status_code,
        success=False,
        latency_ms=latency_ms,
        created_at=datetime.utcnow()
    )
    db.add(usage_entry)
    await db.commit()

async def verify_internal_secret(
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret")
) -> None:
    """Ensure request comes from authorized internal client (e.g. tg-generator-bot)."""
    if not x_internal_secret or x_internal_secret != settings.INTERNAL_API_SECRET:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Invalid internal authorization secret"}
        )

