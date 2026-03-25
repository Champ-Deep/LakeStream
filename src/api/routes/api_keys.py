"""API key management routes for programmatic access (Chrome extension, CLI, etc.)."""

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.middleware.auth import get_current_user
from src.db.pool import get_pool
from src.db.queries.api_keys import (
    create_api_key,
    delete_api_key,
    generate_api_key,
    list_api_keys,
)

router = APIRouter(prefix="/auth/api-keys")
log = structlog.get_logger()


class CreateKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class CreateKeyResponse(BaseModel):
    id: str
    name: str
    key: str  # Raw key — shown ONCE


@router.post("", status_code=201)
async def create_key(body: CreateKeyRequest, user: dict = Depends(get_current_user)):
    """Create a new API key. The raw key is returned only once — store it safely."""
    pool = await get_pool()
    raw_key, key_hash = generate_api_key()

    key_id = await create_api_key(
        pool,
        user_id=UUID(user["user_id"]),
        org_id=UUID(user["org_id"]),
        name=body.name,
        key_hash=key_hash,
    )

    log.info("api_key_created", key_id=str(key_id), name=body.name, user_id=user["user_id"])
    return CreateKeyResponse(id=str(key_id), name=body.name, key=raw_key)


@router.get("")
async def list_keys(user: dict = Depends(get_current_user)):
    """List all API keys for the current organization."""
    pool = await get_pool()
    keys = await list_api_keys(pool, UUID(user["org_id"]))
    return [
        {
            "id": str(k["id"]),
            "name": k["name"],
            "last_used_at": k["last_used_at"].isoformat() if k["last_used_at"] else None,
            "created_at": k["created_at"].isoformat(),
        }
        for k in keys
    ]


@router.delete("/{key_id}", status_code=204)
async def revoke_key(key_id: UUID, user: dict = Depends(get_current_user)):
    """Revoke (delete) an API key."""
    pool = await get_pool()
    deleted = await delete_api_key(pool, key_id, UUID(user["org_id"]))
    if not deleted:
        raise HTTPException(status_code=404, detail="API key not found")
    log.info("api_key_revoked", key_id=str(key_id), user_id=user["user_id"])
