import asyncio
import contextlib
import ipaddress
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

import idna

from domainsmanager_lookup._internal.errors import EndpointDiscoveryError


class StreamWriter(Protocol):
    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...


OpenConnection = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, StreamWriter]]]


@dataclass(frozen=True, slots=True)
class IanaWhoisRecord:
    domain: str | None
    referral_server: str | None
    whois_server: str | None


class IanaWhoisClient:
    """Client for IANA's documented WHOIS endpoint used for registry discovery."""

    HOST = "whois.iana.org"
    PORT = 43

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        max_response_bytes: int = 2 * 1024 * 1024,
        open_connection: OpenConnection | None = None,
    ) -> None:
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes
        self._open_connection = open_connection or asyncio.open_connection

    async def lookup_domain(self, domain: str) -> IanaWhoisRecord:
        query = self._query_bytes(domain)
        writer: StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                self._open_connection(self.HOST, self.PORT), timeout=self._timeout
            )
            writer.write(query)
            await asyncio.wait_for(writer.drain(), timeout=self._timeout)
            body = await self._read_to_eof(reader)
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
            raise EndpointDiscoveryError("IANA WHOIS query failed") from exc
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(OSError, RuntimeError):
                    await writer.wait_closed()
        return self.parse_response(body)

    async def _read_to_eof(self, reader: asyncio.StreamReader) -> bytes:
        chunks: list[bytes] = []
        received = 0
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=self._timeout)
            if not chunk:
                return b"".join(chunks)
            received += len(chunk)
            if received > self._max_response_bytes:
                raise EndpointDiscoveryError("IANA WHOIS response exceeds maximum size")
            chunks.append(chunk)

    @staticmethod
    def parse_response(body: bytes) -> IanaWhoisRecord:
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise EndpointDiscoveryError("IANA WHOIS response is not UTF-8") from exc

        fields: dict[str, set[str]] = {"domain": set(), "refer": set(), "whois": set()}
        for line in text.splitlines():
            if not line or line.startswith("%") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().casefold()
            value = value.strip()
            if key in fields and value:
                fields[key].add(value)

        domain = IanaWhoisClient._single(fields["domain"], "domain")
        referral = IanaWhoisClient._single(fields["refer"], "refer")
        whois = IanaWhoisClient._single(fields["whois"], "whois")
        return IanaWhoisRecord(
            domain=IanaWhoisClient._normalize_domain(domain) if domain else None,
            referral_server=IanaWhoisClient._normalize_host(referral) if referral else None,
            whois_server=IanaWhoisClient._normalize_host(whois) if whois else None,
        )

    @staticmethod
    def _query_bytes(domain: str) -> bytes:
        candidate = domain.strip().lower().rstrip(".")
        if not candidate or any(character.isspace() or ord(character) < 32 for character in candidate):
            raise EndpointDiscoveryError("invalid IANA WHOIS query")
        try:
            return idna.encode(candidate, uts46=True) + b"\r\n"
        except idna.IDNAError as exc:
            raise EndpointDiscoveryError("invalid IANA WHOIS query") from exc

    @staticmethod
    def _single(values: set[str], field: str) -> str | None:
        if len(values) > 1:
            raise EndpointDiscoveryError(f"IANA WHOIS response has conflicting {field} fields")
        return next(iter(values), None)

    @staticmethod
    def _normalize_domain(value: str) -> str:
        candidate = value.strip().lower().lstrip(".").rstrip(".")
        try:
            return idna.encode(candidate, uts46=True).decode("ascii")
        except idna.IDNAError as exc:
            raise EndpointDiscoveryError("IANA WHOIS response has invalid domain") from exc

    @staticmethod
    def _normalize_host(value: str) -> str:
        candidate = value.strip().lower().rstrip(".")
        if not candidate or any(character.isspace() or ord(character) < 33 for character in candidate):
            raise EndpointDiscoveryError("IANA WHOIS response has invalid WHOIS host")
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            pass
        else:
            raise EndpointDiscoveryError("IANA WHOIS response must contain a hostname")
        try:
            normalized = idna.encode(candidate, uts46=True).decode("ascii")
        except idna.IDNAError as exc:
            raise EndpointDiscoveryError("IANA WHOIS response has invalid WHOIS host") from exc
        if len(normalized) > 253 or any(len(label) > 63 for label in normalized.split(".")):
            raise EndpointDiscoveryError("IANA WHOIS response has invalid WHOIS host")
        return normalized
