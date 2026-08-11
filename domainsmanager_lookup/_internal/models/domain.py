from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RegistrarInfo(BaseModel):
    name: str | None = None
    iana_id: int | None = None
    url: str | None = None
    abuse_email: str | None = None
    abuse_phone: str | None = None


class DomainDates(BaseModel):
    registered_at: datetime | None = None
    expires_at: datetime | None = None
    updated_at: datetime | None = None


class DNSSECInfo(BaseModel):
    enabled: bool | None = None


class NormalizedDomain(BaseModel):
    """不包含 I/O 的标准化域名值对象。"""

    model_config = ConfigDict(frozen=True)

    input_name: str
    ascii_name: str
    unicode_name: str
    subdomain: str | None = None
    domain_label: str
    public_suffix: str
    registrable_domain: str
    tld: str


class DomainInfo(BaseModel):
    """由 RDAP 或 WHOIS 统一得到的域名信息。"""

    model_config = ConfigDict(extra="ignore")

    domain: str
    registry_handle: str | None = None
    registrar: RegistrarInfo | None = None
    statuses: list[str] = Field(default_factory=list)
    dates: DomainDates = Field(default_factory=DomainDates)
    nameservers: list[str] = Field(default_factory=list)
    dnssec: DNSSECInfo = Field(default_factory=DNSSECInfo)
    source: Literal["rdap", "whois", "unknown"] = "unknown"
    source_url: str | None = None
    fetched_at: datetime | None = None
    parser_version: str | None = None
