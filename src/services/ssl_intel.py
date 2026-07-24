"""TLS certificate inspection — the "SSL certifications" field.

Pure stdlib (ssl + socket): opens a TLS handshake to domain:443, reads the
peer certificate, and reports issuer/subject/validity/SAN/protocol. No
external dependency, no page fetch — this runs in parallel with the HTTP
fetch and is a domain-level fact (cached, not re-checked per page).
"""

from __future__ import annotations

import asyncio
import socket
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

log = structlog.get_logger()

_TIMEOUT = 5.0
_CERT_DATE_FMT = "%b %d %H:%M:%S %Y %Z"


@dataclass
class SslIntel:
    issuer: str | None = None
    issuer_org: str | None = None
    subject_cn: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    days_until_expiry: int | None = None
    protocol: str | None = None
    san_domains: list[str] = field(default_factory=list)
    self_signed: bool = False


def _rdn_lookup(rdn_sequence, key: str) -> str | None:
    for rdn in rdn_sequence:
        for k, v in rdn:
            if k == key:
                return v
    return None


def _parse_cert_date(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, _CERT_DATE_FMT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _inspect_sync(domain: str, port: int = 443) -> SslIntel:
    intel = SslIntel()
    context = ssl.create_default_context()

    try:
        with socket.create_connection((domain, port), timeout=_TIMEOUT) as sock:
            protocol_name = None
            with context.wrap_socket(sock, server_hostname=domain) as tls:
                cert = tls.getpeercert()
                protocol_name = tls.version()
    except ssl.SSLCertVerificationError as e:
        # Certificate exists but fails verification (expired/self-signed/mismatch)
        # — still worth reporting what we can via an unverified handshake.
        log.debug("ssl_verification_failed", domain=domain, error=str(e))
        intel.self_signed = "self-signed" in str(e).lower() or "self signed" in str(e).lower()
        return _inspect_unverified(domain, port, intel)
    except Exception as e:
        log.debug("ssl_inspect_failed", domain=domain, error=str(e))
        return intel

    intel.protocol = protocol_name
    intel.issuer_org = _rdn_lookup(cert.get("issuer", ()), "organizationName")
    intel.issuer = intel.issuer_org or _rdn_lookup(cert.get("issuer", ()), "commonName")
    intel.subject_cn = _rdn_lookup(cert.get("subject", ()), "commonName")
    intel.valid_from = _parse_cert_date(cert.get("notBefore", ""))
    intel.valid_to = _parse_cert_date(cert.get("notAfter", ""))
    intel.san_domains = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]

    if intel.valid_to:
        intel.days_until_expiry = (intel.valid_to - datetime.now(UTC)).days

    return intel


def _inspect_unverified(domain: str, port: int, intel: SslIntel) -> SslIntel:
    """Fallback when chain verification fails (expired/self-signed/mismatch):
    grab the raw certificate anyway and decode it with `cryptography`, so
    issuer/expiry are still reported — only the verification failure differs.
    """
    context = ssl._create_unverified_context()
    try:
        with socket.create_connection((domain, port), timeout=_TIMEOUT) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as tls:
                der = tls.getpeercert(binary_form=True)
                intel.protocol = tls.version()

        if der:
            from cryptography import x509
            from cryptography.hazmat.backends import default_backend
            from cryptography.x509.oid import NameOID

            cert = x509.load_der_x509_certificate(der, default_backend())

            def _rdn(name, oid) -> str | None:
                attrs = name.get_attributes_for_oid(oid)
                return attrs[0].value if attrs else None

            intel.issuer_org = _rdn(cert.issuer, NameOID.ORGANIZATION_NAME)
            intel.issuer = intel.issuer_org or _rdn(cert.issuer, NameOID.COMMON_NAME)
            intel.subject_cn = _rdn(cert.subject, NameOID.COMMON_NAME)
            intel.valid_from = cert.not_valid_before_utc
            intel.valid_to = cert.not_valid_after_utc
            intel.days_until_expiry = (intel.valid_to - datetime.now(UTC)).days
            try:
                san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
                intel.san_domains = san_ext.value.get_values_for_type(x509.DNSName)
            except x509.ExtensionNotFound:
                pass
            if intel.issuer_org and intel.subject_cn and intel.issuer_org == intel.subject_cn:
                intel.self_signed = True
    except Exception as e:
        log.debug("ssl_unverified_inspect_failed", domain=domain, error=str(e))
    return intel


async def inspect_certificate(domain: str) -> SslIntel:
    """Inspect the TLS certificate for a domain. Never raises."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_inspect_sync, domain), timeout=_TIMEOUT * 2
        )
    except Exception as e:
        log.debug("ssl_intel_failed", domain=domain, error=str(e))
        return SslIntel()
