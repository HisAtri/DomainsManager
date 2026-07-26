import asyncio
from datetime import datetime

from modules.cache.base import DomainResponseCache, RegistryEndpointCache
from modules.models.registry import RegistryEndpoint
from modules.models.response import LookupProtocol, RawLookupResponse


class MemoryDomainResponseCache(DomainResponseCache):
    def __init__(self) -> None:
        self._items: dict[tuple[str, LookupProtocol], RawLookupResponse] = {}
        self._lock = asyncio.Lock()

    async def get_fresh(
        self,
        domain: str,
        protocol: LookupProtocol,
        now: datetime,
    ) -> RawLookupResponse | None:
        async with self._lock:
            item = self._items.get((domain, protocol))
            return item if item is not None and item.is_fresh(now) else None

    async def save(self, response: RawLookupResponse) -> None:
        async with self._lock:
            self._items[(response.domain, response.protocol)] = response


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
