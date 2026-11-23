from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.auth import verify_internal_secret
from app.config import settings
from app.database import get_db
from app.models import ApiKey, ApiUsage, Track, User
from app.schemas import (
    InternalStatsResponse,
    KeyGenerateRequest,
    KeyGenerateResponse,
    KeyItem,
    KeyListResponse,
    KeyRevokeRequest,
    KeyRevokeResponse,
    KeyUsageResponse
)
from app.utils import generate_api_key

router = APIRouter(
    prefix="/internal",
    tags=["Internal Management"],
    dependencies=[Depends(verify_internal_secret)]
)

@router.post("/api-keys/generate", response_model=KeyGenerateResponse)
async def generate_key_endpoint(
    req: KeyGenerateRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Generate or retrieve the user's active API key.
    Enforces One User = Maximum ONE Active API Key.
    """
    # 1. Find or create User
    result = await db.execute(
        select(User).where(User.telegram_user_id == req.telegram_user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        user = User(telegram_user_id=req.telegram_user_id, status="active")
        db.add(user)
        await db.flush()

    # 2. Check for existing active key
    active_result = await db.execute(
        select(ApiKey).where(
            ApiKey.user_id == user.id,
            ApiKey.status == "active"
        )
    )
    active_key = active_result.scalar_one_or_none()

    if active_key:
        # Case C: Is existing key exhausted?
        if active_key.used_requests >= active_key.total_quota:
            active_key.status = "exhausted"
            await db.flush()
        else:
            # Case B: User already has an active, valid key -> Return existing info with full key if available
            return KeyGenerateResponse(
                success=True,
                already_exists=True,
                api_key=active_key.api_key,
                key_prefix=active_key.key_prefix,
                quota_limit=active_key.total_quota,
                used_requests=active_key.used_requests,
                remaining_requests=max(0, active_key.total_quota - active_key.used_requests),
                status=active_key.status,
                message="You already have an active API key."
            )

    # 3. Generate new key (Case A, C, or D)
    plaintext_key, key_hash, key_prefix = generate_api_key()
    new_key = ApiKey(
        user_id=user.id,
        api_key=plaintext_key,
        key_hash=key_hash,
        key_prefix=key_prefix,
        total_quota=getattr(settings, "LIFETIME_QUOTA", 9999999999),
        used_requests=0,
        failed_requests=0,
        status="active"
    )
    db.add(new_key)

    try:
        await db.commit()
    except IntegrityError:
        # Concurrent race condition: another concurrent request created the active key
        await db.rollback()
        winning_result = await db.execute(
            select(ApiKey).where(
                ApiKey.user_id == user.id,
                ApiKey.status == "active"
            )
        )
        winning_key = winning_result.scalar_one_or_none()
        if winning_key:
            return KeyGenerateResponse(
                success=True,
                already_exists=True,
                api_key=winning_key.api_key,
                key_prefix=winning_key.key_prefix,
                quota_limit=winning_key.total_quota,
                used_requests=winning_key.used_requests,
                remaining_requests=max(0, winning_key.total_quota - winning_key.used_requests),
                status=winning_key.status,
                message="You already have an active API key."
            )
        raise

    return KeyGenerateResponse(
        success=True,
        already_exists=False,
        api_key=plaintext_key,
        key_prefix=key_prefix,
        quota_limit=200,
        used_requests=0,
        remaining_requests=200,
        status="active",
        message="Store this API key securely. It will not be shown again."
    )

@router.get("/api-keys/list", response_model=KeyListResponse)
async def list_keys_endpoint(
    telegram_user_id: int = Query(...),
    db: AsyncSession = Depends(get_db)
):
    """List all API keys owned by a specific Telegram user."""
    result = await db.execute(
        select(ApiKey)
        .join(User, ApiKey.user_id == User.id)
        .where(User.telegram_user_id == telegram_user_id)
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()

    items = [
        KeyItem(
            key_prefix=k.key_prefix,
            api_key=k.api_key if k.status == "active" else None,
            total_quota=k.total_quota,
            used_requests=k.used_requests,
            remaining_requests=max(0, k.total_quota - k.used_requests),
            status=k.status,
            created_at=k.created_at
        )
        for k in keys
    ]
    return KeyListResponse(success=True, keys=items)

@router.get("/api-keys/usage", response_model=KeyUsageResponse)
async def get_user_usage_endpoint(
    telegram_user_id: int = Query(...),
    key_prefix: str | None = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Retrieve usage stats for a user's API key."""
    query = (
        select(ApiKey)
        .join(User, ApiKey.user_id == User.id)
        .where(User.telegram_user_id == telegram_user_id)
    )
    if key_prefix:
        query = query.where(ApiKey.key_prefix == key_prefix)
    else:
        query = query.order_by(ApiKey.created_at.desc())

    result = await db.execute(query)
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "KEY_NOT_FOUND", "message": "No API key found for this user"}
        )

    return KeyUsageResponse(
        success=True,
        key_prefix=api_key.key_prefix,
        total_quota=api_key.total_quota,
        used_requests=api_key.used_requests,
        failed_requests=api_key.failed_requests,
        remaining_requests=max(0, api_key.total_quota - api_key.used_requests),
        status=api_key.status
    )

@router.post("/api-keys/revoke", response_model=KeyRevokeResponse)
async def revoke_key_endpoint(
    req: KeyRevokeRequest,
    db: AsyncSession = Depends(get_db)
):
    """Revoke an API key owned by the requesting Telegram user."""
    result = await db.execute(
        select(ApiKey)
        .join(User, ApiKey.user_id == User.id)
        .where(
            User.telegram_user_id == req.telegram_user_id,
            ApiKey.key_prefix == req.key_prefix
        )
    )
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "KEY_NOT_FOUND", "message": "Key not found or not owned by user"}
        )

    if api_key.status == "revoked":
        return KeyRevokeResponse(success=True, message="Key was already revoked")

    api_key.status = "revoked"
    api_key.revoked_at = datetime.utcnow()
    await db.commit()

    return KeyRevokeResponse(success=True, message=f"Key {req.key_prefix} has been revoked successfully")

@router.get("/stats", response_model=InternalStatsResponse)
async def internal_stats_endpoint(
    db: AsyncSession = Depends(get_db)
):
    """Operational statistics for owner administration."""
    total_users = (await db.execute(select(func.count(User.id)))).scalar_one() or 0
    total_keys = (await db.execute(select(func.count(ApiKey.id)))).scalar_one() or 0
    active_keys = (await db.execute(select(func.count(ApiKey.id)).where(ApiKey.status == "active"))).scalar_one() or 0
    total_tracks = (await db.execute(select(func.count(Track.id)))).scalar_one() or 0
    total_usage = (await db.execute(select(func.count(ApiUsage.id)))).scalar_one() or 0

    return InternalStatsResponse(
        success=True,
        total_users=total_users,
        total_keys=total_keys,
        active_keys=active_keys,
        total_tracks=total_tracks,
        total_usage_events=total_usage
    )

