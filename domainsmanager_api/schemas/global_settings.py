from __future__ import annotations

from datetime import datetime

from pydantic import Field

from domainsmanager_api.schemas.admin import StrictModel


class GlobalSettingResponse(StrictModel):
    key: str
    group: str
    label: str
    description: str
    kind: str
    value: int | float | bool | str | None
    configured: bool = False
    version: int = Field(ge=0)
    source: str
    updated_at: datetime | None
    minimum: float | None
    maximum: float | None
    live: bool


class GlobalSettingPatch(StrictModel):
    value: int | float | bool | str | None
