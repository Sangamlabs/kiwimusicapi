import hashlib
import re
import secrets

def generate_api_key() -> tuple[str, str, str]:
    """
    Generate a cryptographically secure Kiwi API key.
    Format: KIWI-<32 hex chars>
    Returns: (plaintext_key, key_hash, key_prefix)
    """
    token = secrets.token_hex(16).upper()
    plaintext_key = f"KIWI-{token}"
    key_hash = hash_api_key(plaintext_key)
    key_prefix = f"KIWI-{token[:8]}..."
    return plaintext_key, key_hash, key_prefix

def hash_api_key(plaintext_key: str) -> str:
    """Compute SHA-256 hash of plaintext API key."""
    return hashlib.sha256(plaintext_key.strip().encode("utf-8")).hexdigest()

def sanitize_slug(name: str) -> str:
    """
    Sanitize slug according to verified Yukisbox rules:
    Only letters, digits, hyphens, and underscores are allowed.
    """
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    cleaned = re.sub(r"_+", "_", cleaned)
    cleaned = cleaned.strip("_-")
    if not cleaned:
        cleaned = f"kiwi_track_{secrets.token_hex(4)}"
    return cleaned[:80]

