from typing import Protocol

from domainsmanager_lookup._internal.models.domain import DomainInfo, NormalizedDomain
from domainsmanager_lookup._internal.models.response import RawLookupResponse


class ResponseParser(Protocol):
    def parse(
        self,
        response: RawLookupResponse,
        domain: NormalizedDomain,
    ) -> DomainInfo:
        ...
