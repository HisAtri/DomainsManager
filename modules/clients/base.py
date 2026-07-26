from typing import Protocol

from modules.models.domain import NormalizedDomain
from modules.models.registry import RegistryEndpoint
from modules.models.response import RawLookupResponse


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
