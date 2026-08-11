from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LookupErrorCode(StrEnum):
    INVALID_DOMAIN = "invalid_domain"
    NOT_FOUND = "not_found"
    UNSUPPORTED = "unsupported"
    RATE_LIMITED = "rate_limited"
    TEMPORARY_FAILURE = "temporary_failure"
    UNEXPECTED_RESPONSE = "unexpected_response"


class DomainIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    ascii_name: str
    unicode_name: str
    registrable_domain: str
    public_suffix: str
    tld: str


class RegistrarSnapshot(BaseModel):
    name: str | None = None
    iana_id: int | None = None
    url: str | None = None
    abuse_email: str | None = None
    abuse_phone: str | None = None


class DomainSnapshot(BaseModel):
    domain: str
    registrar: RegistrarSnapshot | None = None
    statuses: list[str] = Field(default_factory=list)
    registered_at: datetime | None = None
    expires_at: datetime | None = None
    updated_at: datetime | None = None
    nameservers: list[str] = Field(default_factory=list)
    dnssec_enabled: bool | None = None
    source: str
    source_url: str | None = None
    fetched_at: datetime | None = None


class LookupOptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    concurrency: int = Field(default=10, ge=1)
    force_refresh: bool = False
    refresh_endpoint: bool = False


class LookupOutcome(BaseModel):
    input_name: str
    identity: DomainIdentity | None = None
    snapshot: DomainSnapshot | None = None
    error_code: LookupErrorCode | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "LookupOutcome":
        if (self.snapshot is None) == (self.error_code is None):
            raise ValueError("lookup outcome must contain either a snapshot or an error")
        return self

    @property
    def succeeded(self) -> bool:
        return self.snapshot is not None
