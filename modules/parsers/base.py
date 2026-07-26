from typing import Protocol

from modules.models.domain import DomainInfo, NormalizedDomain
from modules.models.response import RawLookupResponse


class ResponseParser(Protocol):
    def parse(
        self,
        response: RawLookupResponse,
        domain: NormalizedDomain,
    ) -> DomainInfo:
        ...
