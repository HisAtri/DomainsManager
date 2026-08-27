from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdminUserResponse(StrictModel):
    id: UUID
    username: str
    email: EmailStr | None
    role: Literal["user", "admin"]
    status: Literal["active", "banned"]
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime
    domain_count: int = Field(ge=0)
    banned_at: datetime | None
    ban_reason: str | None


class AdminUserPageResponse(StrictModel):
    items: list[AdminUserResponse]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class AdminUpdateUserRequest(StrictModel):
    email: EmailStr | None = None


class BanUserRequest(StrictModel):
    reason: str = Field(min_length=3, max_length=512)


class AdminSessionResponse(StrictModel):
    id: UUID
    created_at: datetime
    last_seen_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None
    revoke_reason: str | None


class AdminSessionPageResponse(StrictModel):
    items: list[AdminSessionResponse]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
