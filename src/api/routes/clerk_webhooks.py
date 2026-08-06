"""Clerk webhook receiver — keeps local users/organizations in sync with Clerk.

POST /api/webhooks/clerk. Authentication is the Svix signature alone (Clerk
signs every delivery); the endpoint is otherwise public, so verification
failures must reject before any handler runs.

Handlers are idempotent upserts keyed on clerk_user_id / clerk_org_id —
Svix retries deliveries, and lazy provisioning in resolve_token_context()
may have already created the rows a webhook describes.
"""

import structlog
from fastapi import APIRouter, HTTPException, Request

from src.config.settings import get_settings
from src.db.pool import get_pool
from src.db.queries.users import (
    get_or_create_by_clerk_id,
    get_or_create_org_by_clerk_id,
    get_org_by_clerk_id,
)

log = structlog.get_logger()
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _verify_signature(payload: bytes, headers) -> dict:
    """Verify the Svix signature and return the parsed event."""
    settings = get_settings()
    if not settings.clerk_webhook_signing_secret:
        raise HTTPException(status_code=503, detail="Clerk webhook secret is not configured")

    from svix.webhooks import Webhook, WebhookVerificationError

    try:
        return Webhook(settings.clerk_webhook_signing_secret).verify(
            payload,
            {
                "svix-id": headers.get("svix-id", ""),
                "svix-timestamp": headers.get("svix-timestamp", ""),
                "svix-signature": headers.get("svix-signature", ""),
            },
        )
    except WebhookVerificationError as e:
        raise HTTPException(status_code=400, detail="Invalid webhook signature") from e


def _primary_email(data: dict) -> str:
    addresses = data.get("email_addresses") or []
    primary_id = data.get("primary_email_address_id")
    for addr in addresses:
        if addr.get("id") == primary_id:
            return addr.get("email_address", "")
    if addresses:
        return addresses[0].get("email_address", "")
    return ""


def _full_name(data: dict) -> str:
    return " ".join(p for p in (data.get("first_name"), data.get("last_name")) if p)


async def _upsert_user(pool, data: dict) -> None:
    clerk_user_id = data.get("id", "")
    if not clerk_user_id:
        return
    public_meta = data.get("public_metadata") or {}
    role = public_meta.get("role", "member")
    is_admin = role == "super_admin"
    email = _primary_email(data)

    user = await get_or_create_by_clerk_id(
        pool, clerk_user_id, email, is_admin, role,
        meta_org_id=str(public_meta.get("org_id") or ""),
    )
    # Refresh mutable profile fields; email only when it wouldn't collide with
    # a different local account (unlinked duplicates stay isolated).
    if email:
        clash = await pool.fetchval(
            "SELECT 1 FROM users WHERE email = $1 AND id != $2", email, user.id
        )
        if not clash and user.email != email:
            await pool.execute(
                "UPDATE users SET email = $1, updated_at = NOW() WHERE id = $2",
                email, user.id,
            )
    full_name = _full_name(data)
    if full_name and user.full_name != full_name:
        await pool.execute(
            "UPDATE users SET full_name = $1, updated_at = NOW() WHERE id = $2",
            full_name, user.id,
        )


async def _deactivate_user(pool, data: dict) -> None:
    clerk_user_id = data.get("id", "")
    if not clerk_user_id:
        return
    # Soft only: jobs and scraped_data FK the user row.
    await pool.execute(
        "UPDATE users SET is_active = FALSE, updated_at = NOW() WHERE clerk_user_id = $1",
        clerk_user_id,
    )


async def _upsert_org(pool, data: dict) -> None:
    clerk_org_id = data.get("id", "")
    if not clerk_org_id:
        return
    name = data.get("name", "")
    org = await get_or_create_org_by_clerk_id(pool, clerk_org_id, name)
    if name and org.name != name:
        await pool.execute(
            "UPDATE organizations SET name = $1, updated_at = NOW() WHERE id = $2",
            name, org.id,
        )


async def _detach_org(pool, data: dict) -> None:
    clerk_org_id = data.get("id", "")
    if not clerk_org_id:
        return
    # The local org (and its scoped data) stays; only the Clerk link is cut so
    # a recreated Clerk org with the same id can't silently adopt old data.
    await pool.execute(
        "UPDATE organizations SET clerk_org_id = NULL, updated_at = NOW() "
        "WHERE clerk_org_id = $1",
        clerk_org_id,
    )


async def _sync_membership(pool, data: dict, *, deleted: bool) -> None:
    clerk_org_id = (data.get("organization") or {}).get("id", "")
    org_name = (data.get("organization") or {}).get("name", "")
    clerk_user_id = (data.get("public_user_data") or {}).get("user_id", "")
    clerk_role = (data.get("role") or "").removeprefix("org:")
    if not clerk_org_id or not clerk_user_id:
        return

    user_row = await pool.fetchrow(
        "SELECT id, org_id FROM users WHERE clerk_user_id = $1", clerk_user_id
    )
    if not user_row:
        # Membership for a user we haven't seen; their first sign-in will
        # provision them into the right org from the session token.
        return

    if deleted:
        org = await get_org_by_clerk_id(pool, clerk_org_id)
        if org and user_row["org_id"] == org.id:
            default_org = await pool.fetchval(
                "SELECT id FROM organizations WHERE slug = 'default'"
            )
            if default_org:
                await pool.execute(
                    "UPDATE users SET org_id = $1, role = 'member', updated_at = NOW() "
                    "WHERE id = $2",
                    default_org, user_row["id"],
                )
        return

    org = await get_or_create_org_by_clerk_id(pool, clerk_org_id, org_name)
    local_role = "org_owner" if clerk_role in ("admin", "org_owner") else "member"
    await pool.execute(
        "UPDATE users SET org_id = $1, role = $2, updated_at = NOW() WHERE id = $3",
        org.id, local_role, user_row["id"],
    )


@router.post("/clerk")
async def clerk_webhook(request: Request) -> dict:
    payload = await request.body()
    event = _verify_signature(payload, request.headers)

    event_type = event.get("type", "")
    data = event.get("data") or {}
    pool = await get_pool()

    if event_type in ("user.created", "user.updated"):
        await _upsert_user(pool, data)
    elif event_type == "user.deleted":
        await _deactivate_user(pool, data)
    elif event_type in ("organization.created", "organization.updated"):
        await _upsert_org(pool, data)
    elif event_type == "organization.deleted":
        await _detach_org(pool, data)
    elif event_type in ("organizationMembership.created", "organizationMembership.updated"):
        await _sync_membership(pool, data, deleted=False)
    elif event_type == "organizationMembership.deleted":
        await _sync_membership(pool, data, deleted=True)
    else:
        log.info("clerk_webhook_ignored", event_type=event_type)

    return {"received": True}
