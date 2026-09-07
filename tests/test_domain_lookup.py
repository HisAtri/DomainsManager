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
from domainsmanager_lookup._internal.clients.rdap import RdapClient
from domainsmanager_lookup._internal.clients.whois import WhoisClient
from domainsmanager_lookup._internal.errors import (
    LookupFailedError,
    UpstreamRateLimitError,
)
from domainsmanager_lookup._internal.models.registry import RegistryEndpoint
from domainsmanager_lookup._internal.models.response import RawLookupResponse
from domainsmanager_lookup._internal.normalization.domain import DomainNormalizer
from domainsmanager_lookup._internal.parsers.rdap import RdapParser
from domainsmanager_lookup._internal.parsers.whois import (
    ProfiledWhoisParser,
    WhoisParser,
)
from domainsmanager_lookup._internal.services.domain_lookup import DomainLookupService
from domainsmanager_lookup.endpoint_gate import EndpointRequestGate
from domainsmanager_lookup.memory_store import MemoryLookupStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FakeEndpointProvider:
    def __init__(
        self,
        rdap_url: str | None = "https://rdap.example",
        whois_server: str | None = "whois.example",
    ) -> None:
        self.calls = 0
        self.rdap_url = rdap_url
        self.whois_server = whois_server

    async def discover(self, domain):
        self.calls += 1
        await asyncio.sleep(0.01)
        return RegistryEndpoint(
            key=domain.public_suffix,
            tld=domain.tld,
            whois_server=self.whois_server,
            rdap_urls=[] if self.rdap_url is None else [self.rdap_url],
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


class LegacyWhoisParserTests(unittest.TestCase):
    def test_normalizes_and_deduplicates_epp_statuses(self):
        parser = WhoisParser()
        domain = DomainNormalizer().normalize("example.com")
        response = RawLookupResponse(
            domain="example.com",
            protocol="whois",
            endpoint="whois.example",
            body="""Domain Name: EXAMPLE.COM
Domain Status: clientTransferProhibited https://icann.org/epp#clientTransferProhibited
Domain Status: CLIENTTRANSFERPROHIBITED
Domain Status: ok
""",
            fetched_at=NOW,
            expires_at=NOW,
        )

        result = parser.parse(response, domain)

        self.assertEqual(
            result.statuses,
            ["client transfer prohibited", "active"],
        )


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
            ).discover(DomainNormalizer().normalize("example.co.uk"))

        self.assertEqual(whois_client.name, "example.co.uk")
        self.assertNotIn("/domains/root/db/uk.html", requested_paths)
        self.assertEqual(endpoint.key, "co.uk")
        self.assertEqual(endpoint.whois_server, "whois.nic.uk")
        self.assertEqual(endpoint.rdap_urls, ["https://rdap.nominet.uk/uk"])


class DomainLookupServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limit_retry_time_survives_lookup_fallback_processing(self):
        retry_after = datetime.now(UTC) + timedelta(minutes=3)
        service = DomainLookupService(
            endpoint_provider=FakeEndpointProvider(whois_server=None),
            clients={
                "rdap": FakeClient(
                    "rdap",
                    RDAP_BODY,
                    error=UpstreamRateLimitError(
                        "rdap", "https://rdap.example", retry_after=retry_after
                    ),
                )
            },
            parsers={"rdap": RdapParser()},
            protocol_order=("rdap",),
            endpoint_gate=EndpointRequestGate(MemoryLookupStore()),
        )

        with self.assertRaises(LookupFailedError) as raised:
            await service.lookup("example.com", force_refresh=True)
        self.assertEqual(raised.exception.retry_after, retry_after)

    async def test_uses_registry_related_rdap_for_registrar_expiration(self):
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if request.url.host == "registry.example":
                return httpx.Response(
                    200,
                    json={
                        "objectClassName": "domain",
                        "ldhName": "example.com",
                        "events": [
                            {
                                "eventAction": "expiration",
                                "eventDate": "2027-08-04T00:00:00Z",
                            }
                        ],
                        "links": [
                            {
                                "rel": "related",
                                "type": "application/rdap+json",
                                "href": "https://registrar.example/domain/example.com",
                            }
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "objectClassName": "domain",
                    "ldhName": "example.com",
                    "events": [
                        {
                            "eventAction": "registrar expiration",
                            "eventDate": "2026-08-04T00:00:00Z",
                        }
                    ],
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            service = DomainLookupService(
                response_cache=MemoryDomainResponseCache(),
                endpoint_provider=FakeEndpointProvider("https://registry.example"),
                clients={"rdap": RdapClient(http_client=http_client)},
                parsers={"rdap": RdapParser()},
                protocol_order=("rdap",),
                clock=lambda: NOW,
            )
            first = await service.lookup("example.com")
            second = await service.lookup("example.com")

        self.assertEqual(first.info.dates.registry_expires_at.year, 2027)
        self.assertEqual(first.info.dates.registrar_expires_at.year, 2026)
        self.assertIsNotNone(first.registrar_response)
        self.assertTrue(second.response_cache_hit)
        self.assertEqual(
            requests,
            [
                "https://registry.example/domain/example.com",
                "https://registrar.example/domain/example.com",
            ],
        )

    async def test_registrar_rdap_expiration_fills_registrar_expires_at(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "registry.example":
                return httpx.Response(
                    200,
                    json={
                        "objectClassName": "domain",
                        "ldhName": "example.com",
                        "events": [
                            {
                                "eventAction": "expiration",
                                "eventDate": "2027-08-04T00:00:00Z",
                            }
                        ],
                        "links": [
                            {
                                "rel": "related",
                                "type": "application/rdap+json",
                                "href": "https://registrar.example/domain/example.com",
                            }
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "objectClassName": "domain",
                    "ldhName": "example.com",
                    "events": [
                        {
                            "eventAction": "expiration",
                            "eventDate": "2026-08-04T00:00:00Z",
                        }
                    ],
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            service = DomainLookupService(
                response_cache=MemoryDomainResponseCache(),
                endpoint_provider=FakeEndpointProvider("https://registry.example"),
                clients={"rdap": RdapClient(http_client=http_client)},
                parsers={"rdap": RdapParser()},
                protocol_order=("rdap",),
                clock=lambda: NOW,
            )
            result = await service.lookup("example.com")

        self.assertEqual(result.info.dates.registry_expires_at.year, 2027)
        self.assertEqual(result.info.dates.registrar_expires_at.year, 2026)

    async def test_falls_back_to_registry_expiration_when_registrar_rdap_has_no_dates(
        self,
    ):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "registry.example":
                return httpx.Response(
                    200,
                    json={
                        "objectClassName": "domain",
                        "ldhName": "example.com",
                        "events": [
                            {
                                "eventAction": "expiration",
                                "eventDate": "2027-08-04T00:00:00Z",
                            }
                        ],
                        "links": [
                            {
                                "rel": "related",
                                "type": "application/rdap+json",
                                "href": "https://registrar.example/domain/example.com",
                            }
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "objectClassName": "domain",
                    "ldhName": "example.com",
                    "events": [],
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            service = DomainLookupService(
                response_cache=MemoryDomainResponseCache(),
                endpoint_provider=FakeEndpointProvider("https://registry.example"),
                clients={"rdap": RdapClient(http_client=http_client)},
                parsers={"rdap": RdapParser()},
                protocol_order=("rdap",),
                clock=lambda: NOW,
            )
            result = await service.lookup("example.com")

        self.assertEqual(result.info.dates.registry_expires_at.year, 2027)
        self.assertEqual(result.info.dates.registrar_expires_at.year, 2027)

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
        self.assertEqual(result.info.dates.registry_expires_at.year, 2030)
        self.assertEqual(result.info.dates.registrar_expires_at.year, 2030)
        self.assertEqual(result.info.expiration_status, "active")
        self.assertEqual(rdap.calls, 1)
        self.assertEqual(whois.calls, 1)
        self.assertEqual(provider.calls, 1)

    async def test_uses_whois_directly_when_rdap_endpoint_is_missing(self):
        provider = FakeEndpointProvider(rdap_url=None)
        rdap = FakeClient("rdap", RDAP_BODY)
        whois = FakeClient("whois", WHOIS_BODY)
        service = DomainLookupService(
            endpoint_provider=provider,
            clients={"rdap": rdap, "whois": whois},
            parsers={"rdap": RdapParser(), "whois": WhoisParser()},
            clock=lambda: NOW,
        )

        result = await service.lookup("example.com")

        self.assertEqual(result.info.source, "whois")
        self.assertEqual(rdap.calls, 0)
        self.assertEqual(whois.calls, 1)

    async def test_fails_when_rdap_fallback_has_no_whois_profile(self):
        rdap = FakeClient("rdap", "", error=OSError("RDAP unavailable"))
        service = DomainLookupService(
            endpoint_provider=FakeEndpointProvider(),
            clients={"rdap": rdap, "whois": WhoisClient()},
            parsers={"rdap": RdapParser(), "whois": ProfiledWhoisParser()},
            clock=lambda: NOW,
        )

        with self.assertRaisesRegex(LookupFailedError, "Profile"):
            await service.lookup("example.com")

        self.assertEqual(rdap.calls, 1)

    async def test_fails_without_querying_when_no_protocol_endpoint_exists(self):
        rdap = FakeClient("rdap", RDAP_BODY)
        whois = FakeClient("whois", WHOIS_BODY)
        service = DomainLookupService(
            endpoint_provider=FakeEndpointProvider(
                rdap_url=None,
                whois_server=None,
            ),
            clients={"rdap": rdap, "whois": whois},
            parsers={"rdap": RdapParser(), "whois": WhoisParser()},
            clock=lambda: NOW,
        )

        with self.assertRaises(LookupFailedError):
            await service.lookup("example.com")

        self.assertEqual(rdap.calls, 0)
        self.assertEqual(whois.calls, 0)

    async def test_fails_when_rdap_and_whois_are_unavailable(self):
        rdap = FakeClient("rdap", "", error=OSError("RDAP unavailable"))
        whois = FakeClient("whois", "", error=OSError("WHOIS unavailable"))
        service = DomainLookupService(
            endpoint_provider=FakeEndpointProvider(),
            clients={"rdap": rdap, "whois": whois},
            parsers={"rdap": RdapParser(), "whois": WhoisParser()},
            clock=lambda: NOW,
        )

        with self.assertRaises(LookupFailedError):
            await service.lookup("example.com")

        self.assertEqual(rdap.calls, 1)
        self.assertEqual(whois.calls, 1)

    async def test_registry_rdap_not_found_marks_domain_released_without_whois(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={
                    "errorCode": 404,
                    "title": "Not Found",
                    "description": ["The requested domain does not exist."],
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            whois = FakeClient("whois", WHOIS_BODY)
            service = DomainLookupService(
                endpoint_provider=FakeEndpointProvider("https://registry.example"),
                clients={
                    "rdap": RdapClient(http_client=http_client),
                    "whois": whois,
                },
                parsers={"rdap": RdapParser(), "whois": WhoisParser()},
                clock=lambda: NOW,
            )
            result = await service.lookup("example.com")

        self.assertEqual(result.info.expiration_status, "released")
        self.assertEqual(result.info.expiration_checked_at, NOW)
        self.assertEqual(result.response.status_code, 404)
        self.assertEqual(whois.calls, 0)

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
            lambda domain: RDAP_BODY.replace("example.com", domain.registrable_domain),
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
