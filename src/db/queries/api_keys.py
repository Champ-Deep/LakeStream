import hashlib
import secrets
from datetime import datetime
from uuid import UUID, uuid4

import asyncpg


def generate_api_key() -> tuple[str, str]:
    """Generate a new API key and its SHA256 hash.

    Returns:
        (raw_key, key_hash) — raw_key is shown to the user once, key_hash is stored.
    """
    raw_key = "ls_" + secrets.token_hex(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_hash


async def create_api_key(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    org_id: UUID,
    name: str,
    key_hash: str,
    expires_at: datetime | None = None,
) -> UUID:
    key_id = uuid4()
    await pool.execute(
        """INSERT INTO api_keys (id, user_id, org_id, key_hash, name, expires_at)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        key_id,
        user_id,
        org_id,
        key_hash,
        name,
        expires_at,
    )
    return key_id


async def get_api_key_by_hash(pool: asyncpg.Pool, key_hash: str) -> dict | None:
    """Look up an API key by its SHA256 hash. Returns user/org context or None."""
    row = await pool.fetchrow(
        """SELECT ak.id, ak.user_id, ak.org_id, ak.expires_at
           FROM api_keys ak
           JOIN users u ON u.id = ak.user_id AND u.is_active = TRUE
           WHERE ak.key_hash = $1""",
        key_hash,
    )
    if not row:
        return None
    record = dict(row)
    # Check expiration
    if record["expires_at"] and record["expires_at"] < datetime.now(record["expires_at"].tzinfo):
        return None
    return record


async def list_api_keys(pool: asyncpg.Pool, org_id: UUID) -> list[dict]:
    """List all API keys for an org (never returns the hash)."""
    rows = await pool.fetch(
        """SELECT id, name, last_used_at, expires_at, created_at
           FROM api_keys WHERE org_id = $1 ORDER BY created_at DESC""",
        org_id,
    )
    return [dict(r) for r in rows]


async def delete_api_key(pool: asyncpg.Pool, key_id: UUID, org_id: UUID) -> bool:
    """Delete an API key. Returns True if deleted, False if not found."""
    result = await pool.execute(
        "DELETE FROM api_keys WHERE id = $1 AND org_id = $2", key_id, org_id
    )
    return result == "DELETE 1"


async def touch_api_key(pool: asyncpg.Pool, key_id: UUID) -> None:
    """Update last_used_at timestamp."""
    await pool.execute(
        "UPDATE api_keys SET last_used_at = NOW() WHERE id = $1", key_id
    )
