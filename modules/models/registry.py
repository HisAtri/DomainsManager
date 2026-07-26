from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RegistryEndpoint(BaseModel):
    """某个域名空间可使用的注册局查询端点。"""

    key: str
    tld: str
    whois_server: str | None = None
    rdap_urls: list[str] = Field(default_factory=list)
    source: Literal["iana", "manual"] = "iana"
    fetched_at: datetime
    expires_at: datetime

    def is_fresh(self, now: datetime) -> bool:
        return self.expires_at > now
