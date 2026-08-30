from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(StrictModel):
    username: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=256)
    email: EmailStr | None = None


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=256)
    scope: str = ""


class UpdateCurrentUserRequest(StrictModel):
    email: EmailStr | None = None


class ChangePasswordRequest(StrictModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=6, max_length=256)


class UserSettings(StrictModel):
    locale: Literal["zh-CN", "en-US"] = "zh-CN"
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    default_monitor_enabled: bool = True
    expiration_warning_days: list[Annotated[int, Field(ge=0, le=365)]] = Field(
        default_factory=list,
        max_length=10,
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be a valid IANA timezone") from error
        return value

    @field_validator("expiration_warning_days")
    @classmethod
    def validate_warning_days(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("expiration warning days must be unique")
        return value


class UserSettingsPatch(StrictModel):
    locale: Literal["zh-CN", "en-US"] | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    default_monitor_enabled: bool | None = None
    expiration_warning_days: list[
        Annotated[int, Field(ge=0, le=365)]
    ] | None = Field(default=None, max_length=10)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be a valid IANA timezone") from error
        return value

    @field_validator("expiration_warning_days")
    @classmethod
    def validate_warning_days(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("expiration warning days must be unique")
        return value


class UserResponse(StrictModel):
    id: UUID
    username: str
    email: EmailStr | None
    pending_email: EmailStr | None = None
    email_verified_at: datetime | None = None
    role: Literal["user", "admin"]
    status: Literal["active", "banned"]
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TokenPairResponse(StrictModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(gt=0)


class AuthResultResponse(StrictModel):
    user: UserResponse
    tokens: TokenPairResponse


class EmailVerificationConfirmRequest(StrictModel):
    token: str = Field(min_length=20, max_length=512)


class EmailVerificationResponse(StrictModel):
    status: Literal["pending", "verified"]
    email: EmailStr | None = None
    pending_email: EmailStr | None = None
