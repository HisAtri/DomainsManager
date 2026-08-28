import asyncio
from datetime import datetime

from domainsmanager_lookup._internal.cache.base import (
    DomainResponseCache,
    RegistryEndpointCache,
)
from domainsmanager_lookup._internal.models.registry import RegistryEndpoint
from domainsmanager_lookup._internal.models.response import (
    LookupProtocol,
    RawLookupResponse,
    RdapResponseRole,
)


class MemoryDomainResponseCache(DomainResponseCache):
    def __init__(self) -> None:
        self._items: dict[
            tuple[str, LookupProtocol, RdapResponseRole | None], RawLookupResponse
        ] = {}
        self._lock = asyncio.Lock()

    async def get_fresh(
        self,
        domain: str,
        protocol: LookupProtocol,
        now: datetime,
        rdap_role: RdapResponseRole | None = None,
    ) -> RawLookupResponse | None:
        async with self._lock:
            item = self._items.get((domain, protocol, rdap_role))
            return item if item is not None and item.is_fresh(now) else None

    async def save(self, response: RawLookupResponse) -> None:
        async with self._lock:
            self._items[(response.domain, response.protocol, response.rdap_role)] = (
                response
            )


class MemoryRegistryEndpointCache(RegistryEndpointCache):
    def __init__(self) -> None:
        self._items: dict[str, RegistryEndpoint] = {}
        self._lock = asyncio.Lock()

    async def get_fresh(
        self,
        key: str,
        now: datetime,
    ) -> RegistryEndpoint | None:
        async with self._lock:
            item = self._items.get(key)
            return item if item is not None and item.is_fresh(now) else None

    async def save(self, endpoint: RegistryEndpoint) -> None:
        async with self._lock:
            self._items[endpoint.key] = endpoint
