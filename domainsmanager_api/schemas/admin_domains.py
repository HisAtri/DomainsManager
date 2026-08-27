from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from domainsmanager_api.schemas.domains import (
    DomainIdentityResponse,
    StrictModel,
)
from domainsmanager_api.schemas.tasks import DomainCheckResponse


class UserReferenceResponse(StrictModel):
    id: UUID
    username: str


class AdminManagedDomainResponse(StrictModel):
    id: UUID
    identity: DomainIdentityResponse
    monitor_enabled: bool
    renewal_mode: Literal["automatic", "manual", "unknown"] | None
    notes: str | None
    last_outcome: str | None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    owner: UserReferenceResponse
    deleted_at: datetime | None
    deleted_by_user_id: UUID | None


class AdminDomainPageResponse(StrictModel):
    items: list[AdminManagedDomainResponse]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class AdminUpdateDomainRequest(StrictModel):
    monitor_enabled: bool | None = None
    renewal_mode: Literal["automatic", "manual", "unknown"] | None = None
    notes: str | None = Field(default=None, max_length=5000)


class AdminRefreshDomainRequest(StrictModel):
    force_refresh: bool = True


class CheckStatisticsResponse(StrictModel):
    count_by_outcome: dict[str, int]


class AdminDomainCheckPageResponse(StrictModel):
    items: list[DomainCheckResponse]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    statistics: CheckStatisticsResponse
