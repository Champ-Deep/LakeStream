"""Offline unit tests for DNS-based hosting/email-hosting/CDN heuristics.

Mocks dns.resolver.Resolver.resolve so these run without real network access.
"""

from unittest.mock import patch

import pytest

from src.services import dns_intel


def _fake_answer(values: list[str]):
    return [type("Rdata", (), {"__str__": lambda self, v=v: v})() for v in values]


class FakeResolver:
    def __init__(self, records: dict[str, list[str]]):
        self._records = records
        self.timeout = None
        self.lifetime = None

    def resolve(self, name, rdtype):
        key = (name, rdtype)
        if key not in self._records:
            import dns.resolver

            raise dns.resolver.NXDOMAIN()
        return _fake_answer(self._records[key])


class TestNameserverHostingDetection:
    def test_aws_route53_nameservers(self):
        records = {("acme.com", "NS"): ["ns-1.awsdns-01.com", "ns-2.awsdns-02.org"]}
        with patch("dns.resolver.Resolver", return_value=FakeResolver(records)):
            intel = dns_intel._lookup_sync("acme.com")
        assert intel.web_hosting_provider == "Amazon Web Services (Route 53)"
        assert "NS record" in intel.evidence["web_hosting"]

    def test_godaddy_nameservers(self):
        records = {("acme.com", "NS"): ["ns1.domaincontrol.com", "ns2.domaincontrol.com"]}
        with patch("dns.resolver.Resolver", return_value=FakeResolver(records)):
            intel = dns_intel._lookup_sync("acme.com")
        assert intel.web_hosting_provider == "GoDaddy"

    def test_unknown_nameservers_no_match(self):
        records = {("acme.com", "NS"): ["ns1.somerandomhost.xyz"]}
        with patch("dns.resolver.Resolver", return_value=FakeResolver(records)):
            intel = dns_intel._lookup_sync("acme.com")
        assert intel.web_hosting_provider is None


class TestCdnDetection:
    def test_cloudflare_via_nameserver(self):
        records = {("acme.com", "NS"): ["ns1.cloudflare.com", "ns2.cloudflare.com"]}
        with patch("dns.resolver.Resolver", return_value=FakeResolver(records)):
            intel = dns_intel._lookup_sync("acme.com")
        assert "Cloudflare" in intel.cdn_providers

    def test_cloudfront_via_www_cname(self):
        records = {
            ("acme.com", "NS"): ["ns1.example-dns.net"],
            ("www.acme.com", "CNAME"): ["d123.cloudfront.net"],
        }
        with patch("dns.resolver.Resolver", return_value=FakeResolver(records)):
            intel = dns_intel._lookup_sync("acme.com")
        assert "Amazon CloudFront" in intel.cdn_providers


class TestEmailHostingDetection:
    def test_google_workspace_mx(self):
        records = {("acme.com", "MX"): ["1 aspmx.l.google.com.", "5 alt1.aspmx.l.google.com."]}
        with patch("dns.resolver.Resolver", return_value=FakeResolver(records)):
            intel = dns_intel._lookup_sync("acme.com")
        assert intel.email_hosting_provider == "Google Workspace"

    def test_microsoft_365_mx(self):
        records = {("acme.com", "MX"): ["0 acme-com.mail.protection.outlook.com."]}
        with patch("dns.resolver.Resolver", return_value=FakeResolver(records)):
            intel = dns_intel._lookup_sync("acme.com")
        assert intel.email_hosting_provider == "Microsoft 365"

    def test_no_mx_records(self):
        with patch("dns.resolver.Resolver", return_value=FakeResolver({})):
            intel = dns_intel._lookup_sync("acme.com")
        assert intel.email_hosting_provider is None
        assert intel.mx_hosts == []


class TestAsyncWrapper:
    @pytest.mark.asyncio
    async def test_never_raises_on_failure(self):
        with patch("src.services.dns_intel._lookup_sync", side_effect=RuntimeError("boom")):
            intel = await dns_intel.lookup_domain_intel("acme.com")
        assert intel.web_hosting_provider is None
        assert intel.cdn_providers == []
