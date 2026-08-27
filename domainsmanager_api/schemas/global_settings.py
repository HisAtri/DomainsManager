from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from domainsmanager_api.schemas.admin import StrictModel


class GlobalSettingResponse(StrictModel):
    key: str
    group: str
    label: str
    description: str
    kind: str
    value: Any
    configured: bool = False
    version: int = Field(ge=0)
    source: str
    updated_at: datetime | None
    minimum: float | None
    maximum: float | None
    unit: str | None
    choices: tuple[str, ...] | None = None
    live: bool
    editor: str = "input"
    language: str | None = None
    placeholder: str | None = None


class GlobalSettingPatch(StrictModel):
    value: Any


class GlobalSettingBatchItem(StrictModel):
    key: str
    value: Any
    version: int = Field(ge=0)


class GlobalSettingBatchPatch(StrictModel):
    settings: list[GlobalSettingBatchItem] = Field(min_length=1)
