from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx

from domainsmanager_lookup._internal.errors import ProtocolUnavailableError
from domainsmanager_lookup._internal.models.domain import NormalizedDomain
from domainsmanager_lookup._internal.models.registry import RegistryEndpoint
from domainsmanager_lookup._internal.models.response import (
    RawLookupResponse,
    RdapResponseRole,
)


class RdapClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 20.0,
        cache_ttl: timedelta = timedelta(hours=6),
        not_found_cache_ttl: timedelta = timedelta(minutes=30),
    ) -> None:
        self._http_client = http_client
        self._timeout = timeout
        self._cache_ttl = cache_ttl
        self._not_found_cache_ttl = not_found_cache_ttl

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
        return await self._query_url(
            domain, url, role="registry", follow_redirects=True
        )

    async def query_related(
        self,
        domain: NormalizedDomain,
        url: str,
    ) -> RawLookupResponse:
        self._validate_related_url(url)
        return await self._query_url(
            domain, url, role="registrar", follow_redirects=False
        )

    async def _query_url(
        self,
        domain: NormalizedDomain,
        url: str,
        *,
        role: RdapResponseRole,
        follow_redirects: bool,
    ) -> RawLookupResponse:
        response = await self._get(url, follow_redirects=follow_redirects)
        now = datetime.now(UTC)
        if response.status_code != 404:
            response.raise_for_status()
        return RawLookupResponse(
            domain=domain.registrable_domain,
            protocol="rdap",
            endpoint=url,
            body=response.text,
            status_code=response.status_code,
            content_type=response.headers.get("content-type"),
            fetched_at=now,
            expires_at=now
            + (
                self._not_found_cache_ttl
                if response.status_code == 404
                else self._cache_ttl
            ),
            rdap_role=role,
        )

    async def _get(self, url: str, *, follow_redirects: bool) -> httpx.Response:
        headers = {"accept": "application/rdap+json, application/json"}
        if self._http_client is not None:
            return await self._http_client.get(
                url,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=follow_redirects,
            )
        async with httpx.AsyncClient(follow_redirects=follow_redirects) as client:
            return await client.get(url, headers=headers, timeout=self._timeout)

    @staticmethod
    def _validate_related_url(url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
        ):
            raise ProtocolUnavailableError("注册局提供的注册商 RDAP 链接不安全")
