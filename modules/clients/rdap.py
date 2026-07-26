from datetime import datetime, timedelta, timezone

import httpx

from modules.errors import ProtocolUnavailableError
from modules.models.domain import NormalizedDomain
from modules.models.registry import RegistryEndpoint
from modules.models.response import RawLookupResponse


class RdapClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 20.0,
        cache_ttl: timedelta = timedelta(hours=6),
    ) -> None:
        self._http_client = http_client
        self._timeout = timeout
        self._cache_ttl = cache_ttl

    async def query(
        self,
        domain: NormalizedDomain,
        endpoint: RegistryEndpoint,
    ) -> RawLookupResponse:
        if not endpoint.rdap_urls:
            raise ProtocolUnavailableError(
                f"{domain.public_suffix} 没有可用的 RDAP 端点"
            )

        url = f"{endpoint.rdap_urls[0].rstrip('/')}/domain/{domain.registrable_domain}"
        response = await self._get(url)
        response.raise_for_status()
        now = datetime.now(timezone.utc)
        return RawLookupResponse(
            domain=domain.registrable_domain,
            protocol="rdap",
            endpoint=url,
            body=response.text,
            status_code=response.status_code,
            content_type=response.headers.get("content-type"),
            fetched_at=now,
            expires_at=now + self._cache_ttl,
        )

    async def _get(self, url: str) -> httpx.Response:
        headers = {"accept": "application/rdap+json, application/json"}
        if self._http_client is not None:
            return await self._http_client.get(
                url,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=True,
            )
        async with httpx.AsyncClient(follow_redirects=True) as client:
            return await client.get(url, headers=headers, timeout=self._timeout)
