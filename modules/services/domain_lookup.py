import asyncio
from collections.abc import Callable
from datetime import datetime, timezone

import httpx

from modules.cache.base import DomainResponseCache, RegistryEndpointCache
from modules.cache.memory import MemoryDomainResponseCache, MemoryRegistryEndpointCache
from modules.clients.base import EndpointProvider, RegistryLookupClient
from modules.clients.iana import IanaClient
from modules.clients.rdap import RdapClient
from modules.clients.whois import WhoisClient
from modules.errors import DomainManagerError, LookupFailedError
from modules.models.domain import NormalizedDomain
from modules.models.registry import RegistryEndpoint
from modules.models.response import LookupProtocol, LookupResult
from modules.normalization.domain import DomainNormalizer
from modules.parsers.base import ResponseParser
from modules.parsers.rdap import RdapParser
from modules.parsers.whois import ProfiledWhoisParser


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
        self._clients = clients if clients is not None else {
            "rdap": RdapClient(),
            "whois": WhoisClient(),
        }
        self._parsers = parsers if parsers is not None else {
            "rdap": RdapParser(),
            "whois": ProfiledWhoisParser(),
        }
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
                )
                if response is not None:
                    try:
                        info = self._parsers[protocol].parse(response, domain)
                        return LookupResult(
                            domain=domain,
                            info=info,
                            response=response,
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
                await self._responses.save(response)
                info = self._parsers[protocol].parse(response, domain)
                return LookupResult(
                    domain=domain,
                    info=info,
                    response=response,
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
