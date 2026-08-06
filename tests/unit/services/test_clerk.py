"""Unit tests for Clerk session-JWT verification (src/services/clerk.py).

No network: we generate an RSA keypair in-test, sign tokens locally, and stub
the JWKS client so verify_clerk_token() exercises real signature/expiry/issuer
checks against keys we control.
"""

import time
from types import SimpleNamespace
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.services import clerk as clerk_service
from src.services.clerk import ClerkAuthError, claims_to_context, verify_clerk_token

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_PEM = _PRIVATE_KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_PUBLIC_PEM = _PRIVATE_KEY.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
)

ISSUER = "https://test.clerk.accounts.dev"


class _StubJWKSClient:
    """Stands in for PyJWKClient: always returns our test public key."""

    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=_PUBLIC_PEM)


def _sign(claims: dict) -> str:
    return jwt.encode(claims, _PRIVATE_PEM, algorithm="RS256")


def _base_claims(**overrides) -> dict:
    claims = {
        "sub": "user_2abc123",
        "iss": ISSUER,
        "exp": int(time.time()) + 300,
        "email": "alice@example.com",
    }
    claims.update(overrides)
    return claims


@pytest.fixture
def clerk_env():
    settings = SimpleNamespace(
        clerk_issuer=ISSUER, clerk_jwks_url=f"{ISSUER}/.well-known/jwks.json"
    )
    with (
        patch.object(clerk_service, "_get_jwks_client", return_value=_StubJWKSClient()),
        patch.object(clerk_service, "get_settings", return_value=settings),
    ):
        yield settings


class TestVerifyClerkToken:
    def test_valid_token_returns_claims(self, clerk_env):
        claims = verify_clerk_token(_sign(_base_claims()))
        assert claims["sub"] == "user_2abc123"
        assert claims["email"] == "alice@example.com"

    def test_missing_token_raises(self, clerk_env):
        with pytest.raises(ClerkAuthError):
            verify_clerk_token("")

    def test_expired_token_raises(self, clerk_env):
        token = _sign(_base_claims(exp=int(time.time()) - 60))
        with pytest.raises(ClerkAuthError):
            verify_clerk_token(token)

    def test_token_without_exp_raises(self, clerk_env):
        claims = _base_claims()
        del claims["exp"]
        with pytest.raises(ClerkAuthError):
            verify_clerk_token(_sign(claims))

    def test_wrong_issuer_raises(self, clerk_env):
        token = _sign(_base_claims(iss="https://evil.example.com"))
        with pytest.raises(ClerkAuthError):
            verify_clerk_token(token)

    def test_issuer_not_checked_when_unset(self, clerk_env):
        clerk_env.clerk_issuer = ""
        token = _sign(_base_claims(iss="https://anything.example.com"))
        assert verify_clerk_token(token)["sub"] == "user_2abc123"

    def test_tampered_signature_raises(self, clerk_env):
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_pem = other_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        token = jwt.encode(_base_claims(), other_pem, algorithm="RS256")
        with pytest.raises(ClerkAuthError):
            verify_clerk_token(token)


class TestClaimsToContext:
    def test_basic_mapping(self):
        ctx = claims_to_context(_base_claims())
        assert ctx == {
            "clerk_user_id": "user_2abc123",
            "email": "alice@example.com",
            "role": "member",
            "is_admin": False,
            "clerk_org_id": "",
            "clerk_org_role": "",
            "clerk_org_slug": "",
            "meta_org_id": "",
        }

    def test_v2_compact_org_claim(self):
        ctx = claims_to_context(
            _base_claims(o={"id": "org_9", "rol": "admin", "slg": "acme"})
        )
        assert ctx["clerk_org_id"] == "org_9"
        assert ctx["clerk_org_role"] == "admin"
        assert ctx["clerk_org_slug"] == "acme"

    def test_v1_org_claims_with_prefix_stripped(self):
        ctx = claims_to_context(
            _base_claims(org_id="org_9", org_role="org:admin", org_slug="acme")
        )
        assert ctx["clerk_org_id"] == "org_9"
        assert ctx["clerk_org_role"] == "admin"

    def test_meta_org_id_surfaces(self):
        local_org = "11111111-1111-1111-1111-111111111111"
        ctx = claims_to_context(_base_claims(public_metadata={"org_id": local_org}))
        assert ctx["meta_org_id"] == local_org

    def test_super_admin_via_public_metadata(self):
        ctx = claims_to_context(_base_claims(public_metadata={"role": "super_admin"}))
        assert ctx["is_admin"] is True
        assert ctx["role"] == "super_admin"

    def test_camel_case_metadata_key(self):
        ctx = claims_to_context(_base_claims(publicMetadata={"role": "org_owner"}))
        assert ctx["role"] == "org_owner"
        assert ctx["is_admin"] is False

    def test_email_fallback_from_addresses_list(self):
        claims = _base_claims()
        del claims["email"]
        claims["email_addresses"] = [{"email_address": "bob@example.com"}]
        assert claims_to_context(claims)["email"] == "bob@example.com"

    def test_missing_email_yields_empty_string(self):
        claims = _base_claims()
        del claims["email"]
        assert claims_to_context(claims)["email"] == ""
