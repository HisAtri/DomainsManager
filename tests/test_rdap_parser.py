import json
import unittest
from datetime import UTC, datetime, timedelta

from domainsmanager_lookup._internal.errors import ResponseParseError
from domainsmanager_lookup._internal.models.response import RawLookupResponse
from domainsmanager_lookup._internal.normalization.domain import DomainNormalizer
from domainsmanager_lookup._internal.parsers.rdap import RdapParser

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_response(body: object, *, status_code: int = 200) -> RawLookupResponse:
    return RawLookupResponse(
        domain="example.com",
        protocol="rdap",
        endpoint="https://rdap.example/domain/example.com",
        body=body if isinstance(body, str) else json.dumps(body),
        status_code=status_code,
        fetched_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )


class RdapParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = RdapParser()
        self.domain = DomainNormalizer().normalize("example.com")

    def test_parses_registrar_abuse_and_normalizes_values(self):
        payload = {
            "objectClassName": "DOMAIN",
            "ldhName": "EXAMPLE.COM.",
            "handle": 12345,
            "status": ["ACTIVE", "active", None],
            "events": [
                {
                    "eventAction": "registration",
                    "eventDate": "2021-01-01T00:00:00Z",
                },
                {
                    "eventAction": "registration",
                    "eventDate": "2020-01-01T00:00:00Z",
                },
                {
                    "eventAction": "last changed",
                    "eventDate": "2025-01-01T00:00:00z",
                },
            ],
            "entities": [
                {
                    "handle": "REG-1",
                    "roles": ["REGISTRAR"],
                    "publicIds": [{"type": "IANA Registrar ID", "identifier": "999"}],
                    "vcardArray": [
                        "vcard",
                        [
                            ["FN", {}, "text", "Example Registrar"],
                            ["EMAIL", {}, "text", "fallback@example.test"],
                        ],
                    ],
                    "links": [{"href": "https://registrar.example"}],
                    "entities": [
                        {
                            "roles": ["ABUSE"],
                            "vcardArray": [
                                "vcard",
                                [
                                    [
                                        "email",
                                        {},
                                        "uri",
                                        "mailto:abuse@example.test",
                                    ],
                                    ["tel", {}, "uri", "tel:+1-555-0100"],
                                ],
                            ],
                        }
                    ],
                }
            ],
            "nameservers": [
                {"ldhName": "NS1.EXAMPLE.COM."},
                {"unicodeName": "NS1.example.com"},
                {"ldhName": "not a domain"},
            ],
            "secureDNS": {"delegationSigned": True},
        }

        result = self.parser.parse(make_response(payload), self.domain)

        self.assertEqual(result.domain, "example.com")
        self.assertEqual(result.registry_handle, "12345")
        self.assertEqual(result.statuses, ["active"])
        self.assertEqual(result.dates.registered_at.year, 2020)
        self.assertEqual(result.dates.updated_at.year, 2025)
        self.assertEqual(result.nameservers, ["ns1.example.com"])
        self.assertTrue(result.dnssec.enabled)
        self.assertEqual(result.parser_version, "3")
        self.assertIsNotNone(result.registrar)
        self.assertEqual(result.registrar.name, "Example Registrar")
        self.assertEqual(result.registrar.iana_id, 999)
        self.assertEqual(result.registrar.url, "https://registrar.example")
        self.assertEqual(result.registrar.abuse_email, "abuse@example.test")
        self.assertEqual(result.registrar.abuse_phone, "+1-555-0100")

    def test_accepts_null_optional_fields(self):
        payload = {
            "objectClassName": "domain",
            "ldhName": "example.com",
            "status": None,
            "events": None,
            "entities": None,
            "nameservers": None,
            "secureDNS": None,
        }

        result = self.parser.parse(make_response(payload), self.domain)

        self.assertEqual(result.statuses, [])
        self.assertEqual(result.nameservers, [])
        self.assertIsNone(result.registrar)
        self.assertIsNone(result.dnssec.enabled)

    def test_extracts_registry_and_registrar_expiration_and_related_rdap_link(self):
        payload = {
            "objectClassName": "domain",
            "ldhName": "example.com",
            "events": [
                {"eventAction": "expiration", "eventDate": "2027-08-04T00:00:00Z"},
                {
                    "eventAction": "registrar expiration",
                    "eventDate": "2026-08-04T00:00:00Z",
                },
            ],
            "links": [
                {
                    "rel": "related",
                    "type": "application/rdap+json; charset=utf-8",
                    "href": "https://registrar.example/domain/example.com",
                },
                {
                    "rel": "related",
                    "type": "text/html",
                    "href": "https://registrar.example/domain/example.com",
                },
            ],
        }

        result = self.parser.parse(make_response(payload), self.domain)

        self.assertEqual(result.dates.registry_expires_at.year, 2027)
        self.assertEqual(result.dates.registrar_expires_at.year, 2026)
        self.assertEqual(
            result.registrar_rdap_url, "https://registrar.example/domain/example.com"
        )

    def test_falls_back_to_expiration_when_registrar_expiration_missing(self):
        payload = {
            "objectClassName": "domain",
            "ldhName": "example.com",
            "events": [
                {"eventAction": "expiration", "eventDate": "2027-08-04T00:00:00Z"},
            ],
        }

        result = self.parser.parse(make_response(payload), self.domain)

        self.assertEqual(result.dates.registry_expires_at.year, 2027)
        self.assertEqual(result.dates.registrar_expires_at.year, 2027)
        self.assertEqual(result.dates.expires_at.year, 2027)

    def test_normalizes_unicode_domain_and_nameserver(self):
        domain = DomainNormalizer().normalize("食狮.com")
        payload = {
            "objectClassName": "domain",
            "unicodeName": "食狮.com",
            "nameservers": [{"unicodeName": "NS.食狮.com."}],
        }

        result = self.parser.parse(make_response(payload), domain)

        self.assertEqual(result.domain, "xn--85x722f.com")
        self.assertEqual(result.nameservers, ["ns.xn--85x722f.com"])

    def test_rejects_rdap_error_object(self):
        payload = {
            "errorCode": 404,
            "title": "Not Found",
            "description": ["No domain found"],
        }

        with self.assertRaisesRegex(ResponseParseError, "404.*Not Found"):
            self.parser.parse(make_response(payload, status_code=404), self.domain)

    def test_rejects_non_object_json(self):
        with self.assertRaisesRegex(ResponseParseError, "根节点必须是对象"):
            self.parser.parse(make_response([]), self.domain)

    def test_rejects_invalid_json_or_domain_without_name(self):
        with self.assertRaisesRegex(ResponseParseError, "不是有效的 JSON"):
            self.parser.parse(make_response("{"), self.domain)

        with self.assertRaisesRegex(ResponseParseError, "缺少 ldhName"):
            self.parser.parse(
                make_response({"objectClassName": "domain", "handle": "X"}),
                self.domain,
            )

    def test_rejects_non_domain_or_mismatched_domain(self):
        with self.assertRaisesRegex(ResponseParseError, "不是 domain 对象"):
            self.parser.parse(
                make_response({"objectClassName": "entity", "handle": "X"}),
                self.domain,
            )

        with self.assertRaisesRegex(ResponseParseError, "与查询域名.*不一致"):
            self.parser.parse(
                make_response(
                    {"objectClassName": "domain", "ldhName": "other.example"}
                ),
                self.domain,
            )

    def test_rejects_malformed_standard_field_types(self):
        with self.assertRaisesRegex(ResponseParseError, "'status' 必须是数组"):
            self.parser.parse(
                make_response({"ldhName": "example.com", "status": "active"}),
                self.domain,
            )

        with self.assertRaisesRegex(ResponseParseError, "'secureDNS' 必须是对象"):
            self.parser.parse(
                make_response({"ldhName": "example.com", "secureDNS": []}),
                self.domain,
            )


if __name__ == "__main__":
    unittest.main()
