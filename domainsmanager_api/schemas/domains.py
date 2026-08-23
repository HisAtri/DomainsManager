from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DomainIdentityResponse(StrictModel):
    ascii_name: str
    unicode_name: str
    registrable_domain: str
    public_suffix: str
    tld: str


class ManagedDomainResponse(StrictModel):
    id: UUID
    identity: DomainIdentityResponse
    monitor_enabled: bool
    renewal_mode: Literal["automatic", "manual", "unknown"] | None
    notes: str | None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class CreateDomainRequest(StrictModel):
    name: str = Field(min_length=1, max_length=253)
    monitor_enabled: bool = True


class CreateDomainResult(StrictModel):
    domain: ManagedDomainResponse


class UpdateDomainRequest(StrictModel):
    monitor_enabled: bool | None = None
    renewal_mode: Literal["automatic", "manual", "unknown"] | None = None
    notes: str | None = Field(default=None, max_length=5000)


class DomainPageResponse(StrictModel):
    items: list[ManagedDomainResponse]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class DomainListParameters(StrictModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    query: str | None = Field(default=None, max_length=253)
    monitor_enabled: bool | None = None
    expires_from: datetime | None = None
    expires_to: datetime | None = None
    last_outcome: str | None = Field(default=None, max_length=32)
    sort: Literal[
        "created_at",
        "-created_at",
        "name",
        "-name",
        "expires_at",
        "-expires_at",
        "last_check_at",
        "-last_check_at",
    ] = "name"

    @model_validator(mode="after")
    def validate_expiry_range(self) -> DomainListParameters:
        if (
            self.expires_from is not None
            and self.expires_to is not None
            and self.expires_from > self.expires_to
        ):
            raise ValueError("expires_from must not be after expires_to")
        return self
