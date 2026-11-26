"""Admin password is taken from the environment and never invented."""

import bcrypt
import pytest

from src.config.settings import Settings
from src.db.migrate import (
    _PLACEHOLDER_PASSWORD_HASH,
    _ensure_admin_password,
    configured_admin_password,
    is_unusable_password_hash,
    password_needs_update,
)


class _FakePool:
    def __init__(self, row):
        self.row = row
        self.executed: list[tuple] = []

    async def fetchrow(self, query, *args):
        return self.row

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "UPDATE 1"


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(4)).decode()


def test_settings_admin_password_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    settings = Settings(_env_file=None)
    assert settings.admin_password == ""


def test_configured_admin_password_strips_and_treats_blank_as_unset(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "   ")
    assert configured_admin_password() == ""
    monkeypatch.setenv("ADMIN_PASSWORD", "chosen-password-value")
    assert configured_admin_password() == "chosen-password-value"


def test_placeholder_hash_is_unusable():
    assert is_unusable_password_hash(None)
    assert is_unusable_password_hash("")
    assert is_unusable_password_hash(_PLACEHOLDER_PASSWORD_HASH)
    assert is_unusable_password_hash("not-a-bcrypt-hash")
    assert not is_unusable_password_hash(_hash("some-password"))


def test_password_needs_update_only_when_hash_differs():
    stored = _hash("chosen-password-value")
    assert password_needs_update("chosen-password-value", stored) is False
    assert password_needs_update("different-password-value", stored) is True
    assert password_needs_update("chosen-password-value", _PLACEHOLDER_PASSWORD_HASH) is True


@pytest.mark.asyncio
async def test_unset_password_does_not_activate_placeholder_admin(monkeypatch, capsys):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    pool = _FakePool({"id": "1", "password_hash": _PLACEHOLDER_PASSWORD_HASH, "is_admin": True})

    await _ensure_admin_password(pool)

    assert pool.executed == []
    err = capsys.readouterr().err
    assert "ADMIN_PASSWORD is not set" in err
    assert "cannot log in" in err


@pytest.mark.asyncio
async def test_unset_password_keeps_existing_hash(monkeypatch, capsys):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    pool = _FakePool({"id": "1", "password_hash": _hash("already-set-password"), "is_admin": True})

    await _ensure_admin_password(pool)

    assert pool.executed == []
    out = capsys.readouterr().out
    assert "unchanged" in out


@pytest.mark.asyncio
async def test_set_password_replaces_placeholder(monkeypatch, capsys):
    monkeypatch.setenv("ADMIN_PASSWORD", "chosen-password-value")
    pool = _FakePool({"id": "1", "password_hash": _PLACEHOLDER_PASSWORD_HASH, "is_admin": True})

    await _ensure_admin_password(pool)

    assert len(pool.executed) == 1
    query, args = pool.executed[0]
    assert "password_hash" in query
    assert args[1] == "admin@lakeb2b.internal"
    assert bcrypt.checkpw(b"chosen-password-value", args[0].encode())
    assert "Admin password updated" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_set_password_skips_write_when_hash_matches(monkeypatch):
    stored = _hash("chosen-password-value")
    monkeypatch.setenv("ADMIN_PASSWORD", "chosen-password-value")
    pool = _FakePool({"id": "1", "password_hash": stored, "is_admin": True})

    await _ensure_admin_password(pool)

    assert pool.executed == []


@pytest.mark.asyncio
async def test_missing_admin_user_is_skipped(monkeypatch, capsys):
    monkeypatch.setenv("ADMIN_PASSWORD", "chosen-password-value")
    pool = _FakePool(None)

    await _ensure_admin_password(pool)

    assert pool.executed == []
    assert "not found" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_admin_flag_set_without_writing_password_when_unset(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    pool = _FakePool({"id": "1", "password_hash": _hash("already-set-password"), "is_admin": False})

    await _ensure_admin_password(pool)

    assert len(pool.executed) == 1
    query, args = pool.executed[0]
    assert "is_admin" in query
    assert "password_hash" not in query
    assert args == ("admin@lakeb2b.internal",)
