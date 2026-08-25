from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from domainsmanager_api.schemas.domains import StrictModel


class RefreshDomainRequest(StrictModel):
    force_refresh: bool = False


class TaskErrorResponse(StrictModel):
    code: str
    message: str


class TaskResultResponse(StrictModel):
    code: Literal["refreshed", "data_fresh", "failed"]
    message: str | None
    source_check_id: UUID | None
    fresh_until: datetime | None


class RefreshTaskResponse(StrictModel):
    id: UUID
    kind: Literal["domain_refresh"] = "domain_refresh"
    status: Literal["queued", "running", "success", "info", "warning", "failed"]
    domain_id: UUID
    domain_name: str
    check_id: UUID | None
    error: TaskErrorResponse | None
    result: TaskResultResponse | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


class RefreshTaskPageResponse(StrictModel):
    items: list[RefreshTaskResponse]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class DomainCheckResponse(StrictModel):
    id: UUID
    domain_id: UUID
    checked_at: datetime
    duration_ms: int | None = Field(default=None, ge=0)
    outcome: str
    error_code: str | None
    error_message: str | None
    protocol: Literal["rdap", "whois"] | None
    source: str | None
    snapshot: dict | None
    changed_fields: list[str]
    is_stale: bool
    created_at: datetime


class DomainCheckPageResponse(StrictModel):
    items: list[DomainCheckResponse]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
