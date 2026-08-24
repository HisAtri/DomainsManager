import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from domainsmanager_lookup._internal.clients.iana_whois import IanaWhoisClient
from domainsmanager_lookup._internal.errors import EndpointDiscoveryError
from domainsmanager_lookup._internal.models.domain import NormalizedDomain
from domainsmanager_lookup._internal.models.registry import RegistryEndpoint


class IanaClient:
    """从 IANA WHOIS 和 RDAP Bootstrap 发现注册局端点。"""

    RDAP_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        whois_client: IanaWhoisClient | None = None,
        timeout: float = 15.0,
        cache_ttl: timedelta = timedelta(days=7),
    ) -> None:
        self._http_client = http_client
        self._whois_client = whois_client or IanaWhoisClient(timeout=timeout)
        self._timeout = timeout
        self._cache_ttl = cache_ttl

    async def discover(self, domain: NormalizedDomain) -> RegistryEndpoint:
        whois_result, rdap_result = await asyncio.gather(
            self._discover_whois(domain),
            self._discover_rdap(domain.ascii_name),
            return_exceptions=True,
        )

        whois_server = None if isinstance(whois_result, Exception) else whois_result
        rdap_urls = [] if isinstance(rdap_result, Exception) else rdap_result
        if not whois_server and not rdap_urls:
            errors = [
                str(result)
                for result in (whois_result, rdap_result)
                if isinstance(result, Exception)
            ]
            detail = "; ".join(errors) or "IANA 未提供端点"
            raise EndpointDiscoveryError(
                f"无法发现 {domain.registrable_domain} 的注册局端点：{detail}"
            )

        now = datetime.now(UTC)
        return RegistryEndpoint(
            key=domain.public_suffix,
            tld=domain.tld,
            whois_server=whois_server,
            rdap_urls=rdap_urls,
            source="iana",
            fetched_at=now,
            expires_at=now + self._cache_ttl,
        )

    async def _discover_whois(self, domain: NormalizedDomain) -> str | None:
        record = await self._whois_client.lookup_domain(domain.registrable_domain)
        if record.domain is not None and record.domain != domain.tld:
            raise EndpointDiscoveryError("IANA WHOIS response does not match requested TLD")
        if record.referral_server is not None:
            return record.referral_server
        if domain.tld in {"arpa", "int"} and record.whois_server == "whois.iana.org":
            return record.whois_server
        return None

    async def _discover_rdap(self, ascii_name: str) -> list[str]:
        response = await self._get(self.RDAP_BOOTSTRAP_URL)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

        best_match = ""
        best_urls: list[str] = []
        for service in payload.get("services", []):
            if not isinstance(service, list) or len(service) != 2:
                continue
            suffixes, urls = service
            for suffix in suffixes:
                normalized = str(suffix).lower().rstrip(".")
                if (
                    ascii_name == normalized
                    or ascii_name.endswith(f".{normalized}")
                ) and len(normalized) > len(best_match):
                    best_match = normalized
                    best_urls = [str(url) for url in urls]
        return best_urls

    async def _get(self, url: str) -> httpx.Response:
        if self._http_client is not None:
            return await self._http_client.get(url, timeout=self._timeout)
        async with httpx.AsyncClient(follow_redirects=True) as client:
            return await client.get(url, timeout=self._timeout)
