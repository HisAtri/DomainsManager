import unittest
from datetime import UTC, datetime

from domainsmanager_lookup._internal.models.response import RawLookupResponse
from domainsmanager_lookup._internal.normalization.domain import DomainNormalizer
from domainsmanager_lookup._internal.parsers.whois import ProfiledWhoisParser
from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.builtin.cn import create_cn_profile
from domainsmanager_lookup._internal.whois_profiles.defaults import (
    build_default_whois_registry,
)
from domainsmanager_lookup._internal.whois_profiles.key_value import KeyValueWhoisParser
from domainsmanager_lookup._internal.whois_profiles.models import WhoisResponseStatus
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery
from domainsmanager_lookup._internal.whois_profiles.registry import WhoisProfileRegistry

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_profile(key: str, *suffixes: str) -> WhoisProfile:
    return WhoisProfile(
        key=key,
        suffixes=suffixes,
        query_strategy=StandardWhoisQuery(),
        parser=KeyValueWhoisParser(key=key, version="1"),
    )


class WhoisProfileRegistryTests(unittest.TestCase):
    def test_prefers_public_suffix_then_falls_back_to_tld(self):
        registry = WhoisProfileRegistry()
        registry.register(make_profile("uk", "uk"))
        registry.register(make_profile("co-uk", "co.uk"))
        domain = DomainNormalizer().normalize("example.co.uk")

        self.assertEqual(registry.resolve(domain).key, "co-uk")

        registry.unregister("co-uk")
        self.assertEqual(registry.resolve(domain).key, "uk")

    def test_replaces_profile_without_leaving_old_suffixes(self):
        registry = WhoisProfileRegistry()
        registry.register(make_profile("cc", "cc", "co.cc"))
        registry.register(make_profile("cc", "cc"), replace=True)

        self.assertIsNotNone(registry.get("cc"))
        self.assertIsNone(registry.get("co.cc"))
        self.assertEqual(registry.generation, 2)

    def test_rejects_suffix_owned_by_another_profile(self):
        registry = WhoisProfileRegistry()
        registry.register(make_profile("first", "cc"))

        with self.assertRaises(ValueError):
            registry.register(make_profile("second", "cc"), replace=True)


class CnWhoisProfileTests(unittest.TestCase):
    def setUp(self):
        self.registry = WhoisProfileRegistry()
        self.registry.register(create_cn_profile())
        self.domain = DomainNormalizer().normalize("example.cn")
        self.parser = ProfiledWhoisParser(self.registry)

    def response(self, body: str) -> RawLookupResponse:
        return RawLookupResponse(
            domain="example.cn",
            protocol="whois",
            endpoint="whois.cnnic.cn",
            body=body,
            fetched_at=NOW,
            expires_at=NOW,
        )

    def test_parses_registered_domain(self):
        result = self.parser.parse_result(
            self.response(
                """Domain Name: example.cn
ROID: 20030311s10001s00000000-cn
Sponsoring Registrar: Example Registrar
Registration Time: 2020-01-02 03:04:05
Expiration Time: 2030-01-02 03:04:05
Name Server: ns1.example.cn
Name Server: ns2.example.cn
DNSSEC: signed
"""
            ),
            self.domain,
        )

        self.assertEqual(result.status, WhoisResponseStatus.FOUND)
        self.assertEqual(result.info.registrar.name, "Example Registrar")
        self.assertEqual(result.info.nameservers, ["ns1.example.cn", "ns2.example.cn"])
        self.assertTrue(result.info.dnssec.enabled)
        self.assertEqual(result.info.dates.expires_at.year, 2030)
        self.assertEqual(result.info.dates.registry_expires_at.year, 2030)
        self.assertEqual(result.info.dates.registrar_expires_at.year, 2030)
        self.assertEqual(result.info.dates.registered_at.tzinfo, UTC)

    def test_classifies_not_found_without_fake_domain_info(self):
        result = self.parser.parse_result(
            self.response("No matching record."),
            self.domain,
        )

        self.assertEqual(result.status, WhoisResponseStatus.NOT_FOUND)
        self.assertIsNone(result.info)

    def test_reports_changed_format_instead_of_fabricating_data(self):
        result = self.parser.parse_result(
            self.response("The registry changed this response completely."),
            self.domain,
        )

        self.assertEqual(result.status, WhoisResponseStatus.UNKNOWN)
        self.assertIsNone(result.info)
        self.assertTrue(result.warnings)


class BuiltinWhoisProfileTests(unittest.TestCase):
    def test_co_terms_rate_limited_text_does_not_mask_registered_domain(self):
        parser = ProfiledWhoisParser(build_default_whois_registry())
        domain = DomainNormalizer().normalize("huggingface.co")
        result = parser.parse_result(
            RawLookupResponse(
                domain="huggingface.co",
                protocol="whois",
                endpoint="whois.registry.co",
                body="""Domain Name: HUGGINGFACE.CO
Registry Domain ID: D4157084-CNIC
Registrar: OVH sas
Creation Date: 2016-07-18T16:06:12.0Z
Registry Expiry Date: 2027-07-17T23:59:59.0Z
Name Server: NS-919.AWSDNS-50.NET
DNSSEC: signedDelegation

Access to the Whois and RDAP services is rate limited.
""",
                fetched_at=NOW,
                expires_at=NOW,
            ),
            domain,
        )

        self.assertEqual(result.status, WhoisResponseStatus.FOUND)
        self.assertEqual(result.info.domain, "huggingface.co")
        self.assertEqual(result.info.dates.expires_at.year, 2027)
        self.assertTrue(result.info.dnssec.enabled)

    def test_hk_parser_reads_dnssec_value_from_continuation_line(self):
        parser = ProfiledWhoisParser(build_default_whois_registry())
        domain = DomainNormalizer().normalize("363.hk")
        result = parser.parse_result(
            RawLookupResponse(
                domain="363.hk",
                protocol="whois",
                endpoint="whois.hkirc.hk",
                body="""Domain Name:  363.HK
Domain Name Commencement Date: 10-06-2014
Expiry Date: 10-06-2027
Domain Status: Active

DNSSEC:
  unsigned

Registrar Name: WEST263 INTERNATIONAL LIMITED
""",
                fetched_at=NOW,
                expires_at=NOW,
            ),
            domain,
        )

        self.assertEqual(result.status, WhoisResponseStatus.FOUND)
        self.assertFalse(result.info.dnssec.enabled)

    def test_default_registry_includes_requested_tlds_and_falls_back_from_public_suffix(
        self,
    ):
        registry = build_default_whois_registry()
        normalizer = DomainNormalizer()
        for suffix in ("us", "co", "cc", "ca", "do", "eu", "fr", "hk", "tw", "sh"):
            self.assertEqual(
                registry.resolve(normalizer.normalize(f"example.{suffix}")).key, suffix
            )
        self.assertEqual(
            registry.resolve(normalizer.normalize("example.com.hk")).key, "hk"
        )
        self.assertEqual(
            registry.resolve(normalizer.normalize("example.com.tw")).key, "tw"
        )

    def test_eu_parser_reads_block_sections(self):
        parser = ProfiledWhoisParser(build_default_whois_registry())
        result = parser.parse_result(
            RawLookupResponse(
                domain="example.eu",
                protocol="whois",
                endpoint="whois.eu",
                body="""Domain: example.eu
Registrar:
        Name: Example Registrar

Name servers:
        ns1.example.eu
        ns2.example.eu
""",
                fetched_at=NOW,
                expires_at=NOW,
            ),
            DomainNormalizer().normalize("example.eu"),
        )
        self.assertEqual(result.status, WhoisResponseStatus.FOUND)
        self.assertEqual(result.info.registrar.name, "Example Registrar")
        self.assertEqual(result.info.nameservers, ["ns1.example.eu", "ns2.example.eu"])

    def test_tw_parser_reads_utc_plus_eight_dates_and_nameservers(self):
        parser = ProfiledWhoisParser(build_default_whois_registry())
        result = parser.parse_result(
            RawLookupResponse(
                domain="example.tw",
                protocol="whois",
                endpoint="whois.twnic.net.tw",
                body="""Domain Name: example.tw
Domain Status: ok
Record expires on 2027-12-19 19:23:53 (UTC+8)
Record created on 2023-12-19 19:23:53 (UTC+8)
Domain servers in listed order:
    ns1.example.tw
    ns2.example.tw
Registration Service Provider: Example Registrar
""",
                fetched_at=NOW,
                expires_at=NOW,
            ),
            DomainNormalizer().normalize("example.tw"),
        )
        self.assertEqual(result.status, WhoisResponseStatus.FOUND)
        self.assertEqual(
            result.info.dates.expires_at.utcoffset().total_seconds(), 28800
        )
        self.assertEqual(result.info.nameservers, ["ns1.example.tw", "ns2.example.tw"])


if __name__ == "__main__":
    unittest.main()


class GenericWhoisExpiryTests(unittest.TestCase):
    def test_normalizes_epp_statuses_and_ignores_description_urls(self):
        parser = KeyValueWhoisParser(key="generic", version="1")
        domain = DomainNormalizer().normalize("example.com")
        response = RawLookupResponse(
            domain="example.com",
            protocol="whois",
            endpoint="whois.example",
            body="""Domain Name: example.com
Domain Status: clientTransferProhibited https://icann.org/epp#clientTransferProhibited
Domain Status: CLIENTTRANSFERPROHIBITED
Domain Status: ok https://icann.org/epp#ok
""",
            fetched_at=NOW,
            expires_at=NOW,
        )

        result = parser.parse(response, domain)

        self.assertEqual(
            result.info.statuses,
            ["client transfer prohibited", "active"],
        )

    def test_maps_registry_and_registrar_expiry(self):
        parser = KeyValueWhoisParser(key="generic", version="1")
        domain = DomainNormalizer().normalize("example.com")
        response = RawLookupResponse(
            domain="example.com",
            protocol="whois",
            endpoint="whois.example",
            body="""Domain Name: example.com
Registry Expiry Date: 2027-08-04T00:00:00Z
Registrar Registration Expiration Date: 2026-08-04T00:00:00Z
""",
            fetched_at=NOW,
            expires_at=NOW,
        )
        result = parser.parse(response, domain)
        self.assertEqual(result.info.dates.registry_expires_at.year, 2027)
        self.assertEqual(result.info.dates.registrar_expires_at.year, 2026)
        self.assertEqual(result.info.dates.expires_at.year, 2027)
