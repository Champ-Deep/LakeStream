"""DNS-based domain intelligence: hosting, email hosting, and CDN.

None of this needs a page fetch — it's plain DNS resolution (A, NS, MX,
CNAME) matched against heuristic tables. Deterministic, no external API,
no key required. Results are domain-level facts (not page-level), meant to
be cached long-term (see src/services/enrichment.py).

Heuristic tables here are original — pattern-matching on well-known
nameserver/MX/CNAME substrings, not a vendored fingerprint database.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

import dns.exception
import dns.resolver
import structlog

log = structlog.get_logger()

_TIMEOUT = 4.0

# nameserver substring -> hosting/DNS provider
_NAMESERVER_HOSTS: dict[str, str] = {
    "awsdns": "Amazon Web Services (Route 53)",
    "azure-dns": "Microsoft Azure",
    "domaincontrol.com": "GoDaddy",
    "cloudflare.com": "Cloudflare",
    "googledomains.com": "Google Domains",
    "google.com": "Google Cloud DNS",
    "digitalocean.com": "DigitalOcean",
    "linode.com": "Linode (Akamai)",
    "ovh.net": "OVHcloud",
    "dnsimple.com": "DNSimple",
    "name-services.com": "Namecheap",
    "wixdns.net": "Wix",
    "squarespacedns.com": "Squarespace",
    "shopify.com": "Shopify",
    "vercel-dns.com": "Vercel",
    "netlify": "Netlify",
    "hostgator.com": "HostGator",
    "bluehost.com": "Bluehost",
    "dreamhost.com": "DreamHost",
    "siteground": "SiteGround",
    "hetzner": "Hetzner",
}

# CNAME/nameserver substring -> CDN
_CDN_HOSTS: dict[str, str] = {
    "cloudflare.net": "Cloudflare",
    "cloudflare.com": "Cloudflare",
    "fastly.net": "Fastly",
    "akamaiedge.net": "Akamai",
    "akamai.net": "Akamai",
    "cloudfront.net": "Amazon CloudFront",
    "vercel-dns.com": "Vercel Edge Network",
    "netlify": "Netlify Edge",
    "azureedge.net": "Azure CDN",
    "edgekey.net": "Akamai",
    "cdn77": "CDN77",
    "bunnycdn.com": "BunnyCDN",
    "stackpathcdn.com": "StackPath",
    "kxcdn.com": "KeyCDN",
}

# MX substring -> email hosting provider
_MX_HOSTS: dict[str, str] = {
    "google.com": "Google Workspace",
    "googlemail.com": "Google Workspace",
    "outlook.com": "Microsoft 365",
    "protection.outlook.com": "Microsoft 365",
    "messagingengine.com": "Fastmail",
    "zoho.com": "Zoho Mail",
    "mailgun.org": "Mailgun",
    "sendgrid.net": "SendGrid",
    "pphosted.com": "Proofpoint (relay)",
    "mimecast.com": "Mimecast (relay)",
    "amazonses.com": "Amazon SES",
    "yandex.net": "Yandex Mail",
    "secureserver.net": "GoDaddy Email",
}


@dataclass
class DnsIntel:
    web_hosting_provider: str | None = None
    email_hosting_provider: str | None = None
    cdn_providers: list[str] = field(default_factory=list)
    nameservers: list[str] = field(default_factory=list)
    mx_hosts: list[str] = field(default_factory=list)
    a_records: list[str] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)


def _match(value: str, table: dict[str, str]) -> str | None:
    low = value.lower()
    for needle, label in table.items():
        if needle in low:
            return label
    return None


def _resolve(name: str, rdtype: str) -> list[str]:
    resolver = dns.resolver.Resolver()
    resolver.timeout = _TIMEOUT
    resolver.lifetime = _TIMEOUT
    try:
        answer = resolver.resolve(name, rdtype)
        return [str(r).rstrip(".") for r in answer]
    except (dns.exception.DNSException, OSError):
        return []


def _lookup_sync(domain: str) -> DnsIntel:
    intel = DnsIntel()

    ns_records = _resolve(domain, "NS")
    intel.nameservers = ns_records
    for ns in ns_records:
        if not intel.web_hosting_provider:
            hit = _match(ns, _NAMESERVER_HOSTS)
            if hit:
                intel.web_hosting_provider = hit
                intel.evidence["web_hosting"] = f"NS record: {ns}"
        cdn_hit = _match(ns, _CDN_HOSTS)
        if cdn_hit and cdn_hit not in intel.cdn_providers:
            intel.cdn_providers.append(cdn_hit)
            intel.evidence.setdefault("cdn", f"NS record: {ns}")

    a_records = _resolve(domain, "A")
    intel.a_records = a_records

    # www CNAME often points straight at a CDN/host even when the apex doesn't
    cname_records = _resolve(f"www.{domain}", "CNAME")
    for cname in cname_records:
        cdn_hit = _match(cname, _CDN_HOSTS)
        if cdn_hit and cdn_hit not in intel.cdn_providers:
            intel.cdn_providers.append(cdn_hit)
            intel.evidence.setdefault("cdn", f"CNAME: www.{domain} -> {cname}")
        if not intel.web_hosting_provider:
            host_hit = _match(cname, _NAMESERVER_HOSTS)
            if host_hit:
                intel.web_hosting_provider = host_hit
                intel.evidence["web_hosting"] = f"CNAME: www.{domain} -> {cname}"

    mx_records = _resolve(domain, "MX")
    # MX record format is "priority host.", strip the priority prefix
    mx_hosts = [re.sub(r"^\d+\s+", "", m) for m in mx_records]
    intel.mx_hosts = mx_hosts
    for mx in mx_hosts:
        hit = _match(mx, _MX_HOSTS)
        if hit:
            intel.email_hosting_provider = hit
            intel.evidence["email_hosting"] = f"MX record: {mx}"
            break

    return intel


async def lookup_domain_intel(domain: str) -> DnsIntel:
    """Resolve hosting/email-hosting/CDN for a domain. Never raises."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_lookup_sync, domain), timeout=_TIMEOUT * 5
        )
    except Exception as e:
        log.debug("dns_intel_failed", domain=domain, error=str(e))
        return DnsIntel()
