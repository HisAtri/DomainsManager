import asyncio
import unittest
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from domainsmanager_lookup._internal.cache.memory import (
    MemoryDomainResponseCache,
    MemoryRegistryEndpointCache,
)
from domainsmanager_lookup._internal.clients.iana import IanaClient
from domainsmanager_lookup._internal.clients.iana_whois import IanaWhoisRecord
from domainsmanager_lookup._internal.models.registry import RegistryEndpoint
from domainsmanager_lookup._internal.models.response import RawLookupResponse
from domainsmanager_lookup._internal.normalization.domain import DomainNormalizer
from domainsmanager_lookup._internal.parsers.rdap import RdapParser
from domainsmanager_lookup._internal.parsers.whois import WhoisParser
from domainsmanager_lookup._internal.services.domain_lookup import DomainLookupService

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FakeEndpointProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def discover(self, domain):
        self.calls += 1
        await asyncio.sleep(0.01)
        return RegistryEndpoint(
            key=domain.public_suffix,
            tld=domain.tld,
            whois_server="whois.example",
            rdap_urls=["https://rdap.example"],
            fetched_at=NOW,
            expires_at=NOW + timedelta(days=1),
        )


class FakeClient:
    def __init__(
        self,
        protocol: str,
        body: str | Callable[[object], str],
        error: Exception | None = None,
    ):
        self.protocol = protocol
        self.body = body
        self.error = error
        self.calls = 0

    async def query(self, domain, endpoint):
        self.calls += 1
        if self.error is not None:
            raise self.error
        body = self.body(domain) if callable(self.body) else self.body
        return RawLookupResponse(
            domain=domain.registrable_domain,
            protocol=self.protocol,
            endpoint=f"{self.protocol}.example",
            body=body,
            fetched_at=NOW,
            expires_at=NOW + timedelta(hours=1),
        )


RDAP_BODY = """{
  "ldhName": "example.com",
  "handle": "EXAMPLE-1",
  "status": ["active"],
  "events": [
    {"eventAction": "registration", "eventDate": "2020-01-01T00:00:00Z"},
    {"eventAction": "expiration", "eventDate": "2030-01-01T00:00:00Z"}
  ],
  "nameservers": [{"ldhName": "NS1.EXAMPLE.COM"}],
  "secureDNS": {"delegationSigned": true}
}"""

WHOIS_BODY = """Domain Name: EXAMPLE.COM
Registry Domain ID: EXAMPLE-1
Registrar: Example Registrar
Registrar IANA ID: 123
Creation Date: 2020-01-01T00:00:00Z
Registry Expiry Date: 2030-01-01T00:00:00Z
Domain Status: active
Name Server: NS1.EXAMPLE.COM
DNSSEC: signed
"""


class DomainNormalizerTests(unittest.TestCase):
    def test_normalizes_idn_before_extracting_suffix(self):
        result = DomainNormalizer().normalize("WWW.食狮.公司.CN.")
        self.assertEqual(result.ascii_name, "www.xn--85x722f.xn--55qx5d.cn")
        self.assertEqual(result.registrable_domain, "xn--85x722f.xn--55qx5d.cn")
        self.assertEqual(result.tld, "cn")


class IanaClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_iana_whois_referral_and_suffix_as_cache_key(self):
        requested_paths: list[str] = []

        class FakeIanaWhoisClient:
            async def lookup_domain(self, name: str) -> IanaWhoisRecord:
                self.name = name
                return IanaWhoisRecord(
                    domain="uk",
                    referral_server="whois.nic.uk",
                    whois_server="whois.nic.uk",
                )

        whois_client = FakeIanaWhoisClient()

        def handler(request: httpx.Request) -> httpx.Response:
            requested_paths.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "services": [
                        [["uk"], ["https://rdap.nominet.uk/uk"]],
                    ]
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            endpoint = await IanaClient(
                http_client=http_client, whois_client=whois_client
            ).discover(
                DomainNormalizer().normalize("example.co.uk")
            )

        self.assertEqual(whois_client.name, "example.co.uk")
        self.assertNotIn("/domains/root/db/uk.html", requested_paths)
        self.assertEqual(endpoint.key, "co.uk")
        self.assertEqual(endpoint.whois_server, "whois.nic.uk")
        self.assertEqual(endpoint.rdap_urls, ["https://rdap.nominet.uk/uk"])


class DomainLookupServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_raw_response_cache(self):
        provider = FakeEndpointProvider()
        rdap = FakeClient("rdap", RDAP_BODY)
        service = DomainLookupService(
            response_cache=MemoryDomainResponseCache(),
            endpoint_cache=MemoryRegistryEndpointCache(),
            endpoint_provider=provider,
            clients={"rdap": rdap},
            parsers={"rdap": RdapParser()},
            protocol_order=("rdap",),
            clock=lambda: NOW,
        )

        first = await service.lookup("www.example.com")
        second = await service.lookup("example.com")

        self.assertFalse(first.response_cache_hit)
        self.assertTrue(second.response_cache_hit)
        self.assertEqual(rdap.calls, 1)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(second.info.dates.expires_at.year, 2030)

    async def test_falls_back_from_rdap_to_whois(self):
        provider = FakeEndpointProvider()
        rdap = FakeClient("rdap", "", error=OSError("RDAP unavailable"))
        whois = FakeClient("whois", WHOIS_BODY)
        service = DomainLookupService(
            endpoint_provider=provider,
            clients={"rdap": rdap, "whois": whois},
            parsers={"rdap": RdapParser(), "whois": WhoisParser()},
            clock=lambda: NOW,
        )

        result = await service.lookup("example.com")

        self.assertEqual(result.info.source, "whois")
        self.assertEqual(rdap.calls, 1)
        self.assertEqual(whois.calls, 1)
        self.assertEqual(provider.calls, 1)

    async def test_falls_back_when_rdap_json_has_wrong_root_type(self):
        provider = FakeEndpointProvider()
        rdap = FakeClient("rdap", "[]")
        whois = FakeClient("whois", WHOIS_BODY)
        service = DomainLookupService(
            endpoint_provider=provider,
            clients={"rdap": rdap, "whois": whois},
            parsers={"rdap": RdapParser(), "whois": WhoisParser()},
            clock=lambda: NOW,
        )

        result = await service.lookup("example.com")

        self.assertEqual(result.info.source, "whois")
        self.assertEqual(rdap.calls, 1)
        self.assertEqual(whois.calls, 1)

    async def test_checks_all_protocol_caches_before_network(self):
        cache = MemoryDomainResponseCache()
        await cache.save(
            RawLookupResponse(
                domain="example.com",
                protocol="whois",
                endpoint="whois.cached.example",
                body=WHOIS_BODY,
                fetched_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )
        )
        provider = FakeEndpointProvider()
        rdap = FakeClient("rdap", RDAP_BODY)
        whois = FakeClient("whois", WHOIS_BODY)
        service = DomainLookupService(
            response_cache=cache,
            endpoint_provider=provider,
            clients={"rdap": rdap, "whois": whois},
            parsers={"rdap": RdapParser(), "whois": WhoisParser()},
            clock=lambda: NOW,
        )

        result = await service.lookup("example.com")

        self.assertTrue(result.response_cache_hit)
        self.assertEqual(result.info.source, "whois")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(rdap.calls, 0)
        self.assertEqual(whois.calls, 0)

    async def test_batch_lookup_coalesces_endpoint_discovery(self):
        provider = FakeEndpointProvider()
        rdap = FakeClient(
            "rdap",
            lambda domain: RDAP_BODY.replace(
                "example.com", domain.registrable_domain
            ),
        )
        service = DomainLookupService(
            endpoint_provider=provider,
            clients={"rdap": rdap},
            parsers={"rdap": RdapParser()},
            protocol_order=("rdap",),
            clock=lambda: NOW,
        )

        results = await service.lookup_many(
            ["example.com", "another-example.com"],
            concurrency=2,
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(rdap.calls, 2)


if __name__ == "__main__":
    unittest.main()
