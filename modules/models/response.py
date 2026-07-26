from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from modules.models.domain import DomainInfo, NormalizedDomain

LookupProtocol = Literal["rdap", "whois"]


class RawLookupResponse(BaseModel):
    """可持久化的原始 RDAP/WHOIS 响应。"""

    domain: str
    protocol: LookupProtocol
    endpoint: str
    body: str
    fetched_at: datetime
    expires_at: datetime
    status_code: int | None = None
    content_type: str | None = None

    def is_fresh(self, now: datetime) -> bool:
        return self.expires_at > now


class LookupResult(BaseModel):
    domain: NormalizedDomain
    info: DomainInfo
    response: RawLookupResponse
    response_cache_hit: bool = False
    endpoint_cache_hit: bool = False
