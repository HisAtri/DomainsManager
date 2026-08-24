import asyncio
import unittest

from domainsmanager_lookup._internal.clients.iana_whois import IanaWhoisClient
from domainsmanager_lookup._internal.errors import EndpointDiscoveryError


class IanaWhoisParserTests(unittest.TestCase):
    def test_parses_utf8_key_value_response_and_normalizes_hosts(self):
        record = IanaWhoisClient.parse_response(
            b"% comment\r\nDOMAIN: COM\r\nrefer: WHOIS.VERISIGN-GRS.COM.\r\nwhois: whois.verisign-grs.com\r\nsource: IANA\r\n"
        )

        self.assertEqual(record.domain, "com")
        self.assertEqual(record.referral_server, "whois.verisign-grs.com")
        self.assertEqual(record.whois_server, "whois.verisign-grs.com")

    def test_rejects_conflicting_referrals(self):
        with self.assertRaisesRegex(EndpointDiscoveryError, "conflicting refer"):
            IanaWhoisClient.parse_response(b"refer: whois.one.test\nrefer: whois.two.test\n")

    def test_rejects_non_hostname_referral(self):
        with self.assertRaisesRegex(EndpointDiscoveryError, "invalid WHOIS host"):
            IanaWhoisClient.parse_response(b"refer: https://whois.example.test\n")


class IanaWhoisTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_crlf_and_reads_response_until_eof(self):
        received: list[bytes] = []

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            received.append(await reader.readuntil(b"\r\n"))
            writer.write(b"domain: COM\r\nrefer: whois.")
            await writer.drain()
            writer.write(b"verisign-grs.com\r\n")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            client = IanaWhoisClient(
                open_connection=lambda _host, _port: asyncio.open_connection("127.0.0.1", port)
            )
            record = await client.lookup_domain("example.com")
        finally:
            server.close()
            await server.wait_closed()

        self.assertEqual(received, [b"example.com\r\n"])
        self.assertEqual(record.referral_server, "whois.verisign-grs.com")

    async def test_rejects_oversized_response(self):
        async def handler(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(b"x" * 32)
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            client = IanaWhoisClient(
                max_response_bytes=16,
                open_connection=lambda _host, _port: asyncio.open_connection("127.0.0.1", port),
            )
            with self.assertRaisesRegex(EndpointDiscoveryError, "exceeds maximum size"):
                await client.lookup_domain("example.com")
        finally:
            server.close()
            await server.wait_closed()
