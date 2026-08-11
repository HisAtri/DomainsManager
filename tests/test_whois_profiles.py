import unittest
from datetime import datetime, timezone

from domainsmanager_lookup._internal.models.response import RawLookupResponse
from domainsmanager_lookup._internal.normalization.domain import DomainNormalizer
from domainsmanager_lookup._internal.parsers.whois import ProfiledWhoisParser
from domainsmanager_lookup._internal.whois_profiles.base import WhoisProfile
from domainsmanager_lookup._internal.whois_profiles.builtin.cn import create_cn_profile
from domainsmanager_lookup._internal.whois_profiles.key_value import KeyValueWhoisParser
from domainsmanager_lookup._internal.whois_profiles.models import WhoisResponseStatus
from domainsmanager_lookup._internal.whois_profiles.query import StandardWhoisQuery
from domainsmanager_lookup._internal.whois_profiles.registry import WhoisProfileRegistry


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


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


if __name__ == "__main__":
    unittest.main()
