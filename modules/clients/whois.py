import asyncio
import socket
from datetime import datetime, timedelta, timezone

from modules.errors import ProtocolUnavailableError
from modules.models.domain import NormalizedDomain
from modules.models.registry import RegistryEndpoint
from modules.models.response import RawLookupResponse
from modules.whois_profiles.defaults import get_default_whois_registry
from modules.whois_profiles.registry import WhoisProfileRegistry


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
        raw_body = await asyncio.to_thread(
            self._query_sync,
            query,
            endpoint.whois_server,
        )
        body = profile.query_strategy.decode(raw_body)
        now = datetime.now(timezone.utc)
        return RawLookupResponse(
            domain=domain.registrable_domain,
            protocol="whois",
            endpoint=endpoint.whois_server,
            body=body,
            fetched_at=now,
            expires_at=now + self._cache_ttl,
            content_type="text/plain",
        )

    def _query_sync(self, query: bytes, server: str) -> bytes:
        chunks: list[bytes] = []
        received = 0
        with socket.create_connection((server, 43), timeout=self._timeout) as sock:
            sock.settimeout(self._timeout)
            sock.sendall(query)
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                received += len(chunk)
                if received > self._max_response_bytes:
                    raise ValueError("WHOIS 响应超过允许的最大大小")
                chunks.append(chunk)
        return b"".join(chunks)
