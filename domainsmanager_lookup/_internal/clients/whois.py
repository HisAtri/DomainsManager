import asyncio
from datetime import UTC, datetime, timedelta

from domainsmanager_lookup._internal.errors import ProtocolUnavailableError
from domainsmanager_lookup._internal.models.domain import NormalizedDomain
from domainsmanager_lookup._internal.models.registry import RegistryEndpoint
from domainsmanager_lookup._internal.models.response import RawLookupResponse
from domainsmanager_lookup._internal.whois_profiles.defaults import (
    get_default_whois_registry,
)
from domainsmanager_lookup._internal.whois_profiles.registry import WhoisProfileRegistry


class WhoisClient:
    def __init__(
        self,
        timeout: float = 15.0,
        max_response_bytes: int = 2 * 1024 * 1024,
        cache_ttl: timedelta = timedelta(hours=6),
        profile_registry: WhoisProfileRegistry | None = None,
    ) -> None:
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes
        self._cache_ttl = cache_ttl
        self._profiles = profile_registry or get_default_whois_registry()

    async def query(
        self,
        domain: NormalizedDomain,
        endpoint: RegistryEndpoint,
    ) -> RawLookupResponse:
        if not endpoint.whois_server:
            raise ProtocolUnavailableError(
                f"{domain.public_suffix} 没有可用的 WHOIS 端点"
            )

        profile = self._profiles.resolve(domain)
        query = profile.query_strategy.build_query(domain)
        raw_body = await self._query(query, endpoint.whois_server)
        body = profile.query_strategy.decode(raw_body)
        now = datetime.now(UTC)
        return RawLookupResponse(
            domain=domain.registrable_domain,
            protocol="whois",
            endpoint=endpoint.whois_server,
            body=body,
            fetched_at=now,
            expires_at=now + self._cache_ttl,
            content_type="text/plain",
        )

    async def _query(self, query: bytes, server: str) -> bytes:
        chunks: list[bytes] = []
        received = 0
        # Bound the entire exchange, and close the connection before releasing
        # the endpoint gate on cancellation (a to_thread socket outlives it).
        async with asyncio.timeout(self._timeout):
            reader, writer = await asyncio.open_connection(server, 43)
            try:
                writer.write(query)
                await writer.drain()
                while True:
                    chunk = await reader.read(4096)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > self._max_response_bytes:
                        raise ValueError("WHOIS 响应超过允许的最大大小")
                    chunks.append(chunk)
            finally:
                writer.close()
        return b"".join(chunks)
