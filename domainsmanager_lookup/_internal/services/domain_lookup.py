import asyncio
from collections.abc import Callable
from datetime import datetime, timezone

import httpx

from domainsmanager_lookup._internal.cache.base import (
    DomainResponseCache,
    RegistryEndpointCache,
)
from domainsmanager_lookup._internal.cache.memory import (
    MemoryDomainResponseCache,
    MemoryRegistryEndpointCache,
)
from domainsmanager_lookup._internal.clients.base import (
    EndpointProvider,
    RegistryLookupClient,
)
from domainsmanager_lookup._internal.clients.iana import IanaClient
from domainsmanager_lookup._internal.clients.rdap import RdapClient
from domainsmanager_lookup._internal.clients.whois import WhoisClient
from domainsmanager_lookup._internal.errors import DomainManagerError, LookupFailedError
from domainsmanager_lookup._internal.models.domain import DomainInfo, NormalizedDomain
from domainsmanager_lookup._internal.models.registry import RegistryEndpoint
from domainsmanager_lookup._internal.models.response import (
    LookupProtocol,
    LookupResult,
    RawLookupResponse,
)
from domainsmanager_lookup._internal.normalization.domain import DomainNormalizer
from domainsmanager_lookup._internal.parsers.base import ResponseParser
from domainsmanager_lookup._internal.parsers.rdap import RdapParser
from domainsmanager_lookup._internal.parsers.whois import ProfiledWhoisParser
from domainsmanager_lookup._internal.whois_profiles.defaults import (
    build_default_whois_registry,
)


class DomainLookupService:
    """域名标准化、缓存复用、端点发现、查询和解析的应用服务。"""

    def __init__(
        self,
        response_cache: DomainResponseCache | None = None,
        endpoint_cache: RegistryEndpointCache | None = None,
        normalizer: DomainNormalizer | None = None,
        endpoint_provider: EndpointProvider | None = None,
        clients: dict[LookupProtocol, RegistryLookupClient] | None = None,
        parsers: dict[LookupProtocol, ResponseParser] | None = None,
        protocol_order: tuple[LookupProtocol, ...] = ("rdap", "whois"),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._responses = response_cache or MemoryDomainResponseCache()
        self._endpoints = endpoint_cache or MemoryRegistryEndpointCache()
        self._normalizer = normalizer or DomainNormalizer()
        self._endpoint_provider = endpoint_provider or IanaClient()
        profile_registry = build_default_whois_registry()
        self._clients = (
            clients
            if clients is not None
            else {
                "rdap": RdapClient(),
                "whois": WhoisClient(profile_registry=profile_registry),
            }
        )
        self._parsers = (
            parsers
            if parsers is not None
            else {
                "rdap": RdapParser(),
                "whois": ProfiledWhoisParser(registry=profile_registry),
            }
        )
        self._protocol_order = protocol_order
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._endpoint_locks: dict[str, asyncio.Lock] = {}

        missing = [
            protocol
            for protocol in protocol_order
            if protocol not in self._clients or protocol not in self._parsers
        ]
        if missing:
            raise ValueError(f"查询协议缺少 client 或 parser：{', '.join(missing)}")

    async def lookup(
        self,
        name: str,
        *,
        force_refresh: bool = False,
        refresh_endpoint: bool = False,
    ) -> LookupResult:
        domain = self._normalizer.normalize(name)
        errors: list[str] = []

        if not force_refresh:
            for protocol in self._protocol_order:
                response = await self._responses.get_fresh(
                    domain.registrable_domain,
                    protocol,
                    self._clock(),
                    rdap_role="registry" if protocol == "rdap" else None,
                )
                if response is None and protocol == "rdap":
                    # Accept pre-role cache records and lightweight test clients
                    # while the v1 cache namespace ages out naturally.
                    response = await self._responses.get_fresh(
                        domain.registrable_domain,
                        protocol,
                        self._clock(),
                    )
                if response is not None:
                    try:
                        info = self._parsers[protocol].parse(response, domain)
                        info, registrar_response = (
                            await self._enrich_rdap(domain, info)
                            if protocol == "rdap"
                            else (info, None)
                        )
                        return LookupResult(
                            domain=domain,
                            info=info,
                            response=response,
                            registrar_response=registrar_response,
                            response_cache_hit=True,
                        )
                    except (DomainManagerError, ValueError, TypeError) as exc:
                        errors.append(f"{protocol} 缓存解析失败：{exc}")

        try:
            endpoint, endpoint_cache_hit = await self._get_endpoint(
                domain.public_suffix,
                domain,
                refresh_endpoint,
            )
        except (
            DomainManagerError,
            httpx.HTTPError,
            OSError,
            TimeoutError,
            ValueError,
        ) as exc:
            errors.append(f"端点发现失败：{exc}")
            detail = "; ".join(errors)
            raise LookupFailedError(
                f"查询 {domain.registrable_domain} 失败：{detail}"
            ) from exc

        for protocol in self._protocol_order:
            try:
                response = await self._clients[protocol].query(domain, endpoint)
                try:
                    info = self._parsers[protocol].parse(response, domain)
                except (DomainManagerError, ValueError, TypeError):
                    mark_unusable = getattr(self._responses, "mark_unusable", None)
                    if mark_unusable is not None:
                        try:
                            await mark_unusable(response, "response parsing failed")
                        except Exception:
                            pass
                    raise
                try:
                    await self._responses.save(response)
                except Exception:
                    pass
                info, registrar_response = (
                    await self._enrich_rdap(domain, info)
                    if protocol == "rdap"
                    else (info, None)
                )
                return LookupResult(
                    domain=domain,
                    info=info,
                    response=response,
                    registrar_response=registrar_response,
                    endpoint_cache_hit=endpoint_cache_hit,
                )
            except (
                DomainManagerError,
                httpx.HTTPError,
                OSError,
                TimeoutError,
                ValueError,
                TypeError,
            ) as exc:
                errors.append(f"{protocol} 查询失败：{exc}")

        detail = "; ".join(errors) or "没有配置查询协议"
        raise LookupFailedError(f"查询 {domain.registrable_domain} 失败：{detail}")

    async def lookup_many(
        self,
        names: list[str],
        *,
        concurrency: int = 10,
        force_refresh: bool = False,
    ) -> list[LookupResult]:
        """按输入顺序批量查询域名，并限制同时进行的网络任务数。"""
        if concurrency < 1:
            raise ValueError("concurrency 必须大于 0")
        semaphore = asyncio.Semaphore(concurrency)

        async def limited_lookup(name: str) -> LookupResult:
            async with semaphore:
                return await self.lookup(name, force_refresh=force_refresh)

        return list(await asyncio.gather(*(limited_lookup(name) for name in names)))

    async def _get_endpoint(
        self,
        key: str,
        domain: NormalizedDomain,
        refresh: bool,
    ) -> tuple[RegistryEndpoint, bool]:
        if not refresh:
            cached = await self._endpoints.get_fresh(key, self._clock())
            if cached is not None:
                return cached, True

        lock = self._endpoint_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not refresh:
                cached = await self._endpoints.get_fresh(key, self._clock())
                if cached is not None:
                    return cached, True

            endpoint = await self._endpoint_provider.discover(domain)
            await self._endpoints.save(endpoint)
            return endpoint, False

    async def _enrich_rdap(
        self,
        domain: NormalizedDomain,
        registry_info: DomainInfo,
    ) -> tuple[DomainInfo, RawLookupResponse | None]:
        """Fetch the registry-advertised registrar RDAP document when available."""
        related_url = registry_info.registrar_rdap_url
        client = self._clients.get("rdap")
        parser = self._parsers.get("rdap")
        if related_url is None or not isinstance(client, RdapClient) or parser is None:
            return registry_info, None
        try:
            response = await self._responses.get_fresh(
                domain.registrable_domain,
                "rdap",
                self._clock(),
                rdap_role="registrar",
            )
            if response is None:
                response = await client.query_related(domain, related_url)
                await self._responses.save(response)
            registrar_info = parser.parse(response, domain)
        except (
            DomainManagerError,
            httpx.HTTPError,
            OSError,
            TimeoutError,
            ValueError,
            TypeError,
        ):
            return registry_info, None

        dates = registry_info.dates.model_copy(
            update={
                "registrar_expires_at": registrar_info.dates.registrar_expires_at,
            }
        )
        return registry_info.model_copy(update={"dates": dates}), response
