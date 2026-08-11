from typing import Protocol

from domainsmanager_lookup._internal.models.domain import NormalizedDomain
from domainsmanager_lookup._internal.models.registry import RegistryEndpoint
from domainsmanager_lookup._internal.models.response import RawLookupResponse


class EndpointProvider(Protocol):
    async def discover(self, domain: NormalizedDomain) -> RegistryEndpoint:
        ...


class RegistryLookupClient(Protocol):
    async def query(
        self,
        domain: NormalizedDomain,
        endpoint: RegistryEndpoint,
    ) -> RawLookupResponse:
        ...
