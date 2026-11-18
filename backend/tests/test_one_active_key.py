import pytest
from app.config import settings
from sqlalchemy import select, update
from app.models import ApiKey, User

@pytest.mark.asyncio
async def test_case_a_new_user_generates_key(client):
    """Case A: User has never generated a key -> receives new active key with 200 quota."""
    resp = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 111001},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["already_exists"] is False
    assert data["api_key"].startswith("KIWI-")
    assert data["key_prefix"].startswith("KIWI-")
    assert data["quota_limit"] == 200
    assert data["used_requests"] == 0
    assert data["remaining_requests"] == 200
    assert data["status"] == "active"

@pytest.mark.asyncio
async def test_case_b_existing_active_key_prevents_duplicate(client):
    """Case B: User with active valid key cannot generate duplicate key, returns active key for copying."""
    # First generation
    resp1 = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 111002},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert resp1.status_code == 200
    key1_data = resp1.json()
    key1_prefix = key1_data["key_prefix"]
    key1_secret = key1_data["api_key"]

    # Second generation attempt
    resp2 = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 111002},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["success"] is True
    assert data2["already_exists"] is True
    assert data2["api_key"] == key1_secret  # Copiable active key returned
    assert data2["key_prefix"] == key1_prefix
    assert data2["remaining_requests"] == 200
    assert data2["status"] == "active"

@pytest.mark.asyncio
async def test_case_c_exhausted_key_allows_replacement(client, db_session):
    """Case C: Exhausted key allows generating a replacement key with fresh quota."""
    # 1. Generate key
    resp1 = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 111003},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert resp1.status_code == 200
    prefix1 = resp1.json()["key_prefix"]

    # 2. Simulate exhausting the key (used = 200)
    await db_session.execute(
        update(ApiKey)
        .where(ApiKey.key_prefix == prefix1)
        .values(used_requests=200)
    )
    await db_session.commit()

    # 3. Generate again -> Should detect exhaustion, mark old key exhausted, issue fresh key
    resp2 = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 111003},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["success"] is True
    assert data2["already_exists"] is False
    assert data2["api_key"] is not None
    assert data2["key_prefix"] != prefix1
    assert data2["remaining_requests"] == 200
    assert data2["used_requests"] == 0

    # Verify old key is exhausted in DB
    old_key = (await db_session.execute(select(ApiKey).where(ApiKey.key_prefix == prefix1))).scalar_one()
    assert old_key.status == "exhausted"

@pytest.mark.asyncio
async def test_case_d_revocation_and_reissuance(client):
    """Case D: Revoking an active key allows immediate generation of a fresh key."""
    # 1. Generate initial key
    resp1 = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 111004},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert resp1.status_code == 200
    prefix1 = resp1.json()["key_prefix"]

    # 2. Revoke key
    rev_resp = await client.post(
        "/internal/api-keys/revoke",
        json={"telegram_user_id": 111004, "key_prefix": prefix1},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert rev_resp.status_code == 200
    assert rev_resp.json()["success"] is True

    # 3. Generate replacement key
    resp2 = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 111004},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["already_exists"] is False
    assert data2["api_key"] is not None
    assert data2["key_prefix"] != prefix1
    assert data2["remaining_requests"] == 200

@pytest.mark.asyncio
async def test_user_isolation_cannot_revoke_other_user_key(client):
    """Security: User A cannot revoke User B's API key."""
    # User A generates key
    respA = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 111005},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    prefixA = respA.json()["key_prefix"]

    # User B attempts to revoke User A's key
    respB = await client.post(
        "/internal/api-keys/revoke",
        json={"telegram_user_id": 111006, "key_prefix": prefixA},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert respB.status_code == 404
    assert respB.json()["error"]["code"] == "KEY_NOT_FOUND"

@pytest.mark.asyncio
async def test_list_keys_for_user(client):
    """Ensure /internal/api-keys/list returns all historical keys for the specific user."""
    # Generate and revoke key 1
    resp1 = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 111007},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    prefix1 = resp1.json()["key_prefix"]
    await client.post(
        "/internal/api-keys/revoke",
        json={"telegram_user_id": 111007, "key_prefix": prefix1},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )

    # Generate key 2
    resp2 = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 111007},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    prefix2 = resp2.json()["key_prefix"]

    # List keys
    list_resp = await client.get(
        "/internal/api-keys/list?telegram_user_id=111007",
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    assert list_resp.status_code == 200
    keys = list_resp.json()["keys"]
    assert len(keys) == 2
    prefixes = [k["key_prefix"] for k in keys]
    assert prefix1 in prefixes
    assert prefix2 in prefixes
    # Verify active key has api_key populated
    active_key = [k for k in keys if k["status"] == "active"][0]
    assert active_key["api_key"] is not None


@pytest.mark.asyncio
async def test_flexible_authentication_methods(client):
    """Ensure API accepts X-API-Key, KIWIAPI header, Authorization Bearer, and ?kiwiapi= query param."""
    gen = await client.post(
        "/internal/api-keys/generate",
        json={"telegram_user_id": 111008},
        headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET}
    )
    key = gen.json()["api_key"]

    # 1. Header KIWIAPI (as copied from .env)
    r1 = await client.get("/v1/usage", headers={"KIWIAPI": key})
    assert r1.status_code == 200

    # 2. Header Authorization: Bearer
    r2 = await client.get("/v1/usage", headers={"Authorization": f"Bearer {key}"})
    assert r2.status_code == 200

    # 3. Query parameter ?kiwiapi=
    r3 = await client.get(f"/v1/usage?kiwiapi={key}")
    assert r3.status_code == 200

    # 4. Standard X-API-Key
    r4 = await client.get("/v1/usage", headers={"X-API-Key": key})
    assert r4.status_code == 200

