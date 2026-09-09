import unittest
from datetime import UTC, datetime

from domainsmanager_lookup._internal.models.response import RawLookupResponse
from domainsmanager_lookup._internal.normalization.domain import DomainNormalizer
from domainsmanager_lookup._internal.parsers.whois import ProfiledWhoisParser
from domainsmanager_lookup._internal.whois_profiles.defaults import (
    build_default_whois_registry,
)
from domainsmanager_lookup._internal.whois_profiles.models import WhoisResponseStatus

NOW = datetime(2026, 9, 9, tzinfo=UTC)


class CcTldWhoisProfileTests(unittest.TestCase):
    def setUp(self):
        self.registry = build_default_whois_registry()
        self.parser = ProfiledWhoisParser(self.registry)
        self.normalizer = DomainNormalizer()

    def parse(self, domain_name: str, endpoint: str, body: str):
        domain = self.normalizer.normalize(domain_name)
        return self.parser.parse_result(
            RawLookupResponse(
                domain=domain.registrable_domain,
                protocol="whois",
                endpoint=endpoint,
                body=body,
                fetched_at=NOW,
                expires_at=NOW,
            ),
            domain,
        )

    def test_de_query_and_parser_match_live_denic_response(self):
        domain = self.normalizer.normalize("google.de")
        profile = self.registry.resolve(domain)

        self.assertEqual(
            profile.query_strategy.build_query(domain),
            b"-T dn,ace google.de\r\n",
        )

        result = self.parse(
            "google.de",
            "whois.denic.de",
            """Domain: google.de
Nserver: ns1.google.com
Nserver: ns2.google.com
Nserver: ns3.google.com
Nserver: ns4.google.com
Status: connect
Changed: 2018-03-12T21:44:25+01:00
""",
        )

        self.assertEqual(result.status, WhoisResponseStatus.FOUND)
        self.assertEqual(result.info.domain, "google.de")
        self.assertEqual(result.info.statuses, ["connect"])
        self.assertEqual(
            result.info.nameservers,
            [
                "ns1.google.com",
                "ns2.google.com",
                "ns3.google.com",
                "ns4.google.com",
            ],
        )
        self.assertEqual(result.info.dates.updated_at.utcoffset().total_seconds(), 3600)

    def test_br_parser_matches_live_registro_br_response(self):
        result = self.parse(
            "google.com.br",
            "whois.registro.br",
            """domain:      google.com.br
nserver:     ns1.google.com
nserver:     ns2.google.com
nserver:     ns3.google.com
nserver:     ns4.google.com
created:     19990518 #162310
changed:     20260421
expires:     20270518
status:      published

nic-hdl-br:  DOADM17
created:     20100520
changed:     20220228
""",
        )

        self.assertEqual(result.status, WhoisResponseStatus.FOUND)
        self.assertEqual(result.info.domain, "google.com.br")
        self.assertEqual(result.info.statuses, ["published"])
        self.assertEqual(result.info.dates.registered_at.year, 1999)
        self.assertEqual(result.info.dates.updated_at.year, 2026)
        self.assertEqual(result.info.dates.expires_at.year, 2027)
        self.assertEqual(result.info.dates.registry_expires_at.year, 2027)

    def test_nl_parser_matches_live_sidn_response(self):
        result = self.parse(
            "google.nl",
            "whois.domain-registry.nl",
            """Domain name: google.nl
Status:      active

Registrar:
   MarkMonitor Inc.
   1120 S. Rackham Way Suite 300 IDAHO

DNSSEC:      no

Domain nameservers:
   ns1.google.com
   ns2.google.com
   ns3.google.com
   ns4.google.com

Creation Date: 1999-05-27
Updated Date: 2025-04-18
""",
        )

        self.assertEqual(result.status, WhoisResponseStatus.FOUND)
        self.assertEqual(result.info.registrar.name, "MarkMonitor Inc.")
        self.assertEqual(result.info.statuses, ["active"])
        self.assertEqual(result.info.dates.registered_at.year, 1999)
        self.assertFalse(result.info.dnssec.enabled)
        self.assertEqual(len(result.info.nameservers), 4)

    def test_au_parser_matches_live_auda_response(self):
        result = self.parse(
            "google.com.au",
            "whois.auda.org.au",
            """Domain Name: google.com.au
Registry Domain ID: 92bb371462a94cd584a36765406a795d-AU
Registrar URL: https://whois-webform.markmonitor.com/whois/
Last Modified: 2026-08-15T21:50:56Z
Registrar Name: MarkMonitor Corporate Services Inc
Registrar Abuse Contact Email: abusecomplaints@markmonitor.com
Registrar Abuse Contact Phone: +1.2083895770
Status: clientDeleteProhibited https://identitydigital.au/whois-status-codes#clientDeleteProhibited
Status: serverTransferProhibited https://identitydigital.au/whois-status-codes#serverTransferProhibited
Name Server: ns1.google.com
Name Server: ns2.google.com
DNSSEC: unsigned
""",
        )

        self.assertEqual(result.status, WhoisResponseStatus.FOUND)
        self.assertEqual(
            result.info.registry_handle,
            "92bb371462a94cd584a36765406a795d-AU",
        )
        self.assertEqual(
            result.info.registrar.name,
            "MarkMonitor Corporate Services Inc",
        )
        self.assertEqual(
            result.info.statuses,
            ["client delete prohibited", "server transfer prohibited"],
        )
        self.assertFalse(result.info.dnssec.enabled)

    def test_in_parser_matches_live_nixi_registry_response(self):
        result = self.parse(
            "google.in",
            "whois.nixiregistry.in",
            """Domain Name: google.in
Registry Domain ID: D21089-IN
Registrar URL: http://www.markmonitor.com
Updated Date: 2026-05-26T12:16:48.154Z
Creation Date: 2005-02-14T20:35:14.765Z
Registry Expiry Date: 2027-02-14T20:35:14.765Z
Registrar: MarkMonitor Inc.
Registrar IANA ID: 292
Registrar Abuse Contact Email: abusecomplaints@markmonitor.com
Domain Status: clientUpdateProhibited https://icann.org/epp#clientUpdateProhibited
Domain Status: clientTransferProhibited https://icann.org/epp#clientTransferProhibited
Name Server: ns1.google.com
Name Server: ns2.google.com
DNSSEC: unsigned
""",
        )

        self.assertEqual(result.status, WhoisResponseStatus.FOUND)
        self.assertEqual(result.info.registry_handle, "D21089-IN")
        self.assertEqual(result.info.registrar.iana_id, 292)
        self.assertEqual(result.info.dates.registered_at.year, 2005)
        self.assertEqual(result.info.dates.expires_at.year, 2027)
        self.assertFalse(result.info.dnssec.enabled)

    def test_ru_parser_matches_live_tci_response(self):
        result = self.parse(
            "yandex.ru",
            "whois.tcinet.ru",
            """domain:        YANDEX.RU
nserver:       ns1.yandex.ru. 213.180.193.1, 2a02:6b8::1
nserver:       ns2.yandex.ru. 93.158.134.1, 2a02:6b8:0:1::1
state:         REGISTERED, DELEGATED, VERIFIED
registrar:     RU-CENTER-RU
created:       1997-09-23T09:45:07Z
paid-till:     2026-09-30T21:00:00Z
source:        TCI
""",
        )

        self.assertEqual(result.status, WhoisResponseStatus.FOUND)
        self.assertEqual(result.info.registrar.name, "RU-CENTER-RU")
        self.assertEqual(result.info.statuses, ["registered", "delegated", "verified"])
        self.assertEqual(
            result.info.nameservers,
            ["ns1.yandex.ru", "ns2.yandex.ru"],
        )
        self.assertEqual(result.info.dates.registered_at.year, 1997)
        self.assertEqual(result.info.dates.expires_at.year, 2026)

    def test_pl_parser_matches_live_nask_response(self):
        result = self.parse(
            "google.pl",
            "whois.dns.pl",
            """DOMAIN NAME:                    google.pl
nameservers:                    ns1.google.com.
                                ns2.google.com.
                                ns3.google.com.
                                ns4.google.com.
created:                        2002.09.19 13:00:00
last modified:                  2026.08.17 12:47:01
renewal date:                   2027.09.18 14:00:00
dnssec:                         Unsigned

REGISTRAR:
Markmonitor, Inc.
1120 S. Rackham Way, Suite 300
""",
        )

        self.assertEqual(result.status, WhoisResponseStatus.FOUND)
        self.assertEqual(result.info.registrar.name, "Markmonitor, Inc.")
        self.assertEqual(result.info.dates.registered_at.year, 2002)
        self.assertEqual(result.info.dates.updated_at.year, 2026)
        self.assertEqual(result.info.dates.expires_at.year, 2027)
        self.assertEqual(len(result.info.nameservers), 4)
        self.assertFalse(result.info.dnssec.enabled)

    def test_it_parser_matches_live_nic_it_response(self):
        result = self.parse(
            "google.it",
            "whois.nic.it",
            """Domain:             google.it
Status:             ok
Signed:             no
Created:            1999-12-10 00:00:00
Last Update:        2026-06-09 23:13:34
Expire Date:        2027-04-21

Registrant
  Organization:     Google Ireland Holdings Unlimited Company

Registrar
  Organization:     MarkMonitor International Limited
  Name:             MARKMONITOR-REG
  Web:              https://www.markmonitor.com/
  DNSSEC:           no

Nameservers
  ns1.google.com
  ns2.google.com
  ns3.google.com
  ns4.google.com
""",
        )

        self.assertEqual(result.status, WhoisResponseStatus.FOUND)
        self.assertEqual(
            result.info.registrar.name,
            "MarkMonitor International Limited",
        )
        self.assertEqual(result.info.registrar.url, "https://www.markmonitor.com/")
        self.assertEqual(result.info.statuses, ["active"])
        self.assertEqual(result.info.dates.registered_at.year, 1999)
        self.assertEqual(result.info.dates.expires_at.year, 2027)
        self.assertEqual(len(result.info.nameservers), 4)
        self.assertFalse(result.info.dnssec.enabled)

    def test_live_not_found_formats_are_classified(self):
        cases = (
            ("probe.de", "whois.denic.de", "Domain: probe.de\nStatus: free\n"),
            (
                "probe.com.br",
                "whois.registro.br",
                "% No match for probe.com.br\n",
            ),
            ("probe.nl", "whois.domain-registry.nl", "probe.nl is free\n"),
            ("probe.com.au", "whois.auda.org.au", "Domain not found.\n"),
            (
                "probe.in",
                "whois.nixiregistry.in",
                ">>> Domain probe.in is available for registration\n",
            ),
            (
                "probe.ru",
                "whois.tcinet.ru",
                "No entries found for the selected source(s).\n",
            ),
            (
                "probe.pl",
                "whois.dns.pl",
                "No information available about domain name probe.pl in the Registry NASK database.\n",
            ),
            (
                "probe.it",
                "whois.nic.it",
                "Domain:             probe.it\nStatus:             AVAILABLE\n",
            ),
        )

        for domain, endpoint, body in cases:
            with self.subTest(domain=domain):
                result = self.parse(domain, endpoint, body)
                self.assertEqual(result.status, WhoisResponseStatus.NOT_FOUND)
                self.assertIsNone(result.info)

    def test_default_registry_resolves_all_requested_cctlds(self):
        for suffix in ("de", "br", "nl", "au", "in", "ru", "pl", "it"):
            with self.subTest(suffix=suffix):
                domain = self.normalizer.normalize(f"example.{suffix}")
                self.assertEqual(self.registry.resolve(domain).key, suffix)

        self.assertEqual(
            self.registry.resolve(self.normalizer.normalize("example.com.br")).key,
            "br",
        )
        self.assertEqual(
            self.registry.resolve(self.normalizer.normalize("example.com.au")).key,
            "au",
        )


if __name__ == "__main__":
    unittest.main()
