"""域名模型兼容入口；新代码优先从 ``modules.models`` 导入。"""

from pydantic import BaseModel, Field

from modules.models.domain import (
    DNSSECInfo,
    DomainDates,
    DomainInfo,
    NormalizedDomain,
    RegistrarInfo,
)
from modules.normalization.domain import DomainNormalizer


class Domain(BaseModel):
    """旧版可变模型，保留用于兼容现有调用。"""

    name: str
    punycode: str | None = Field(None, description="Punycode 域名")
    idn: str | None = Field(None, description="IDN 域名")
    public_suffix: str | None = Field(None, description="公共后缀；如 com")
    private_suffix: str | None = Field(
        None,
        description="可注册域名；如 example.com",
    )
    subdomain: str | None = Field(None, description="子域名；如 www")
    domain: str | None = Field(None, description="域名主体；如 example")

    def resolve_suffix(self) -> "Domain":
        normalized = DomainNormalizer().normalize(self.name)
        self.punycode = normalized.ascii_name
        self.idn = normalized.unicode_name
        self.public_suffix = normalized.public_suffix
        self.private_suffix = normalized.registrable_domain
        self.subdomain = normalized.subdomain
        self.domain = normalized.domain_label
        return self


__all__ = [
    "DNSSECInfo",
    "Domain",
    "DomainDates",
    "DomainInfo",
    "NormalizedDomain",
    "RegistrarInfo",
]
