from __future__ import annotations

from datetime import datetime

from pydantic import Field

from domainsmanager_api.schemas.admin import StrictModel


class GlobalSettingResponse(StrictModel):
    key: str
    value: int
    version: int = Field(ge=0)
    source: str
    updated_at: datetime | None


class GlobalSettingPatch(StrictModel):
    value: int = Field(ge=60, le=2_592_000)
