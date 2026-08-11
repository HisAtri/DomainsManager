"""Legacy domain model compatibility entry point."""

from pydantic import BaseModel, Field

from domainsmanager_lookup._internal.models.domain import (
    DNSSECInfo,
    DomainDates,
    DomainInfo,
    NormalizedDomain,
    RegistrarInfo,
)
from domainsmanager_lookup._internal.normalization.domain import DomainNormalizer


class Domain(BaseModel):
    """Legacy mutable domain model retained for compatibility."""

    name: str
    punycode: str | None = Field(None, description="Punycode domain")
    idn: str | None = Field(None, description="IDN domain")
    public_suffix: str | None = None
    private_suffix: str | None = None
    subdomain: str | None = None
    domain: str | None = None

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
