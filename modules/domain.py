from datetime import datetime
from typing import Literal

import tldextract
from pydantic import BaseModel, ConfigDict, Field


class RegistrarInfo(BaseModel):
    """注册商信息"""

    name: str | None = None
    iana_id: int | None = None
    url: str | None = None

    abuse_email: str | None = None
    abuse_phone: str | None = None

    def __str__(self) -> str:
        return (f"Registrar: {self.name}\n"
        f"IANA ID: {self.iana_id}\n"
        f"Registrar URL: {self.url}\n"
        f"Abuse Email: {self.abuse_email}\n"
        f"Abuse Phone: {self.abuse_phone}\n")


class DomainDates(BaseModel):
    """域名生命周期时间"""

    registered_at: datetime | None = None
    expires_at: datetime | None = None
    updated_at: datetime | None = None

    def __str__(self) -> str:
        return (f"Registered At: {self.registered_at}\n"
        f"Expires At: {self.expires_at}\n"
        f"Updated At: {self.updated_at}\n")


class DNSSECInfo(BaseModel):
    """DNSSEC 信息"""

    enabled: bool | None = None
    
    def __str__(self) -> str:
        return (f"DNSSEC: {self.enabled}\n")


class DomainInfo(BaseModel):
    """规范化后的域名核心信息"""

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

    def __str__(self) -> str:
        """
        返回标准的域名信息文本内容
        """
        return (f"Domain: {self.domain}\n"
        f"Registry Handle: {self.registry_handle}\n"
        f"Registrar\n {self.registrar}\n"
        f"Statuses: {self.statuses}\n"
        f"{self.dates}\n"
        f"Nameservers:\n {'  \n'.join(self.nameservers)}\n"
        f"{self.dnssec}\n"
        f"Source: {self.source}\n"
        f"Source URL: {self.source_url}\n"
        f"Fetched At: {self.fetched_at}\n")


class Domain(BaseModel):
    name: str
    punycode: str | None = Field(None, description="Punycode 域名")
    idn: str | None = Field(None, description="IDN 域名")
    public_suffix: str | None = Field(None, description="公共后缀；如 com")
    private_suffix: str | None = Field(None, description="私有后缀；如 example.com")
    subdomain: str | None = Field(None, description="子域名；如 www")
    domain: str | None = Field(None, description="域名；如 example")

    def resolve_suffix(self) -> "Domain":
        """解析域名的子域、主体、公共后缀和可注册域名。"""
        extracted = tldextract.extract(self.name)

        self.public_suffix = extracted.suffix or None
        self.private_suffix = extracted.top_domain_under_public_suffix or None
        self.subdomain = extracted.subdomain or None
        self.domain = extracted.domain or None

        return self
