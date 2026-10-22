"""One-time import of local users/orgs into Clerk (C3 of the Clerk cutover).

Order matters: organizations first (backfilling organizations.clerk_org_id),
then users (bcrypt hashes carry over, so passwords keep working), then org
memberships. Every step is idempotent — rows that already have a clerk_*_id
are skipped, so the script is safe to re-run after a partial failure.

Usage:
    python scripts/clerk_import_users.py            # dry run (default)
    python scripts/clerk_import_users.py --execute  # write to Clerk + DB
    python scripts/clerk_import_users.py --execute --include-inactive

Requires CLERK_SECRET_KEY and DATABASE_URL in the environment (or .env).
Writes a CSV report next to the script: clerk_import_report.csv.
"""

import argparse
import asyncio
import csv
import os
import sys
import time
from pathlib import Path

import asyncpg
import httpx

CLERK_API = "https://api.clerk.com/v1"
# Clerk rate limit is roughly 20 req/10s on most plans; stay under it.
REQUEST_INTERVAL_S = 0.6

BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")
PLACEHOLDER_HASHES = ("", "REPLACE_WITH_BCRYPT_HASH")


class ClerkClient:
    """Minimal Clerk Backend API wrapper with 429 backoff."""

    def __init__(self, secret_key: str, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(
            base_url=CLERK_API,
            headers={"Authorization": f"Bearer {secret_key}"},
            timeout=30.0,
            transport=transport,
        )

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        for attempt in range(5):
            resp = self._client.request(method, path, **kwargs)
            if resp.status_code != 429:
                return resp
            retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
            time.sleep(retry_after)
        return resp

    def create_organization(self, name: str, local_org_id: str) -> httpx.Response:
        return self.request(
            "POST", "/organizations",
            json={"name": name, "private_metadata": {"local_org_id": local_org_id}},
        )

    def create_user(self, payload: dict) -> httpx.Response:
        return self.request("POST", "/users", json=payload)

    def find_user_by_email(self, email: str) -> dict | None:
        resp = self.request("GET", "/users", params={"email_address": [email]})
        if resp.status_code == 200:
            users = resp.json()
            if isinstance(users, list) and users:
                return users[0]
        return None

    def create_membership(self, clerk_org_id: str, clerk_user_id: str, role: str) -> httpx.Response:
        return self.request(
            "POST", f"/organizations/{clerk_org_id}/memberships",
            json={"user_id": clerk_user_id, "role": role},
        )


def build_user_payload(user: dict) -> tuple[dict, str]:
    """Build the Clerk POST /users payload. Returns (payload, note).

    note is "" for a clean import, or a report annotation such as
    "needs-password-reset" when the local hash can't be carried over.
    """
    first_name, _, last_name = (user["full_name"] or "").partition(" ")
    payload: dict = {
        "email_address": [user["email"]],
        "public_metadata": {
            "role": "super_admin" if user["is_admin"] else user["role"],
            "org_id": str(user["org_id"]),
        },
    }
    if first_name:
        payload["first_name"] = first_name
    if last_name:
        payload["last_name"] = last_name

    pw_hash = user["password_hash"] or ""
    if pw_hash.startswith(BCRYPT_PREFIXES):
        payload["password_hasher"] = "bcrypt"
        payload["password_digest"] = pw_hash
        payload["skip_password_checks"] = True
        return payload, ""
    # '' (Clerk lazy-provision hack), the seed placeholder, or a non-bcrypt
    # hash: import without a password and flag for reset.
    payload["skip_password_requirement"] = True
    note = "needs-password-reset"
    if pw_hash and pw_hash not in PLACEHOLDER_HASHES:
        note = "needs-password-reset (non-bcrypt hash)"
    return payload, note


def check_email_collisions(users: list[dict]) -> list[str]:
    """Emails that collide after case-normalization — resolve before importing."""
    seen: dict[str, str] = {}
    collisions = []
    for u in users:
        norm = u["email"].strip().lower()
        if norm in seen and seen[norm] != u["email"]:
            collisions.append(f"{seen[norm]} <-> {u['email']}")
        seen.setdefault(norm, u["email"])
    return collisions


async def import_orgs(pool, clerk: ClerkClient, *, execute: bool, report: list[dict]) -> None:
    rows = await pool.fetch(
        "SELECT id, name, slug FROM organizations WHERE clerk_org_id IS NULL ORDER BY created_at"
    )
    for org in rows:
        if not execute:
            report.append(
                {"kind": "org", "name": org["name"], "action": "would-create", "note": ""}
            )
            continue
        resp = clerk.create_organization(org["name"], str(org["id"]))
        if resp.status_code in (200, 201):
            clerk_org_id = resp.json()["id"]
            await pool.execute(
                "UPDATE organizations SET clerk_org_id = $1, updated_at = NOW() WHERE id = $2",
                clerk_org_id, org["id"],
            )
            report.append(
                {"kind": "org", "name": org["name"], "action": "created", "note": clerk_org_id}
            )
        else:
            report.append({
                "kind": "org", "name": org["name"], "action": "FAILED",
                "note": f"{resp.status_code}: {resp.text[:200]}",
            })
        time.sleep(REQUEST_INTERVAL_S if execute else 0)


async def import_users(
    pool, clerk: ClerkClient, *, execute: bool, include_inactive: bool, report: list[dict]
) -> None:
    active_filter = "" if include_inactive else "AND is_active = TRUE"
    rows = await pool.fetch(
        f"SELECT id, email, password_hash, full_name, role, is_admin, is_active, org_id "
        f"FROM users WHERE clerk_user_id IS NULL {active_filter} ORDER BY created_at"
    )
    users = [dict(r) for r in rows]

    collisions = check_email_collisions(users)
    if collisions:
        for c in collisions:
            report.append(
                {"kind": "user", "name": c, "action": "SKIPPED", "note": "case-collision"}
            )
        print(f"WARNING: {len(collisions)} case-colliding email pairs skipped — resolve manually.")
        colliding = {c.split(" <-> ")[1] for c in collisions}
        users = [u for u in users if u["email"] not in colliding]

    for user in users:
        payload, note = build_user_payload(user)
        if not execute:
            report.append({
                "kind": "user", "name": user["email"], "action": "would-create", "note": note,
            })
            continue

        resp = clerk.create_user(payload)
        if resp.status_code in (200, 201):
            clerk_user_id = resp.json()["id"]
            action = "created"
        elif resp.status_code == 422 and "email" in resp.text.lower():
            # Already in Clerk (e.g. they signed up before the import ran).
            # Backfill the link only — this is the deliberate, script-side
            # equivalent of link-by-email; the runtime flag stays off.
            existing = clerk.find_user_by_email(user["email"])
            if not existing:
                report.append({
                    "kind": "user", "name": user["email"], "action": "FAILED",
                    "note": "422 but lookup found no user",
                })
                continue
            clerk_user_id = existing["id"]
            action = "linked-existing"
        else:
            report.append({
                "kind": "user", "name": user["email"], "action": "FAILED",
                "note": f"{resp.status_code}: {resp.text[:200]}",
            })
            continue

        await pool.execute(
            "UPDATE users SET clerk_user_id = $1, updated_at = NOW() WHERE id = $2",
            clerk_user_id, user["id"],
        )
        report.append({"kind": "user", "name": user["email"], "action": action, "note": note})
        time.sleep(REQUEST_INTERVAL_S)


async def import_memberships(
    pool, clerk: ClerkClient, *, execute: bool, report: list[dict]
) -> None:
    rows = await pool.fetch(
        """
        SELECT u.email, u.role, u.clerk_user_id, o.clerk_org_id
        FROM users u JOIN organizations o ON o.id = u.org_id
        WHERE u.clerk_user_id IS NOT NULL AND o.clerk_org_id IS NOT NULL
        ORDER BY u.created_at
        """
    )
    for row in rows:
        clerk_role = "org:admin" if row["role"] == "org_owner" else "org:member"
        if not execute:
            report.append({
                "kind": "membership", "name": row["email"],
                "action": "would-add", "note": clerk_role,
            })
            continue
        resp = clerk.create_membership(row["clerk_org_id"], row["clerk_user_id"], clerk_role)
        if resp.status_code in (200, 201):
            action = "added"
            note = clerk_role
        elif resp.status_code == 422:
            action = "already-member"  # idempotent re-run
            note = ""
        else:
            action = "FAILED"
            note = f"{resp.status_code}: {resp.text[:200]}"
        report.append({"kind": "membership", "name": row["email"], "action": action, "note": note})
        time.sleep(REQUEST_INTERVAL_S)


async def run(execute: bool, include_inactive: bool) -> int:
    secret = os.environ.get("CLERK_SECRET_KEY", "")
    if execute and not secret:
        print("CLERK_SECRET_KEY is required for --execute.")
        return 1
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        from src.config.settings import get_settings

        database_url = get_settings().database_url

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    clerk = ClerkClient(secret or "sk_dry_run")
    report: list[dict] = []
    try:
        await import_orgs(pool, clerk, execute=execute, report=report)
        await import_users(
            pool, clerk, execute=execute, include_inactive=include_inactive, report=report
        )
        await import_memberships(pool, clerk, execute=execute, report=report)
    finally:
        await pool.close()

    report_path = Path(__file__).parent / "clerk_import_report.csv"
    with open(report_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["kind", "name", "action", "note"])
        writer.writeheader()
        writer.writerows(report)

    failed = sum(1 for r in report if r["action"] == "FAILED")
    resets = sum(1 for r in report if "needs-password-reset" in r["note"])
    mode = "EXECUTE" if execute else "DRY RUN"
    print(f"[{mode}] {len(report)} actions, {failed} failed, {resets} need password reset.")
    print(f"Report: {report_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true", help="Write to Clerk + DB (default: dry run)"
    )
    parser.add_argument(
        "--include-inactive", action="store_true", help="Also import is_active=false users"
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.execute, args.include_inactive)))
