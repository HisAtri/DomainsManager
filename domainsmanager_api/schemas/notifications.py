from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator

EventType = Literal[
    "domain.expiration_warning",
    "domain.status_changed",
    "domain.query_failed",
]


def validate_webhook_url(value: HttpUrl | None) -> HttpUrl | None:
    if value is None:
        return None
    if value.scheme != "https" or value.port != 443:
        raise ValueError("webhook_url must use HTTPS on port 443")
    if value.username is not None or value.password is not None or value.fragment:
        raise ValueError("webhook_url must not contain credentials or a fragment")
    return value


def validate_webhook_name(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ValueError("webhook_name must not be blank")
    return value


class CreateNotificationRuleRequest(BaseModel):
    domain_id: UUID | None = None
    event_type: EventType
    days_before: int | None = Field(default=None, ge=0, le=365)
    channel: Literal["email", "webhook"]
    webhook_url: HttpUrl | None = None
    webhook_name: str | None = Field(default=None, min_length=1, max_length=128)

    _validate_webhook_url = field_validator("webhook_url")(validate_webhook_url)
    _validate_webhook_name = field_validator("webhook_name")(validate_webhook_name)


class UpdateNotificationRuleRequest(BaseModel):
    domain_id: UUID | None = None
    event_type: EventType | None = None
    days_before: int | None = Field(default=None, ge=0, le=365)
    channel: Literal["email", "webhook"] | None = None
    webhook_url: HttpUrl | None = None
    webhook_name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None

    _validate_webhook_url = field_validator("webhook_url")(validate_webhook_url)
    _validate_webhook_name = field_validator("webhook_name")(validate_webhook_name)


class NotificationRuleResponse(BaseModel):
    id: UUID
    domain_id: UUID | None
    event_type: str
    days_before: int | None
    channel: str
    webhook_url: str | None
    webhook_name: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class NotificationDeliveryResponse(BaseModel):
    id: UUID
    domain_id: UUID
    event_type: str
    channel: str
    status: Literal["pending", "running", "sent", "dead_letter", "skipped"]
    attempt_count: int
    available_at: datetime | None
    sent_at: datetime | None
    failure_reason: str | None
    outcome: str | None
    response_status: str | None
    created_at: datetime
    updated_at: datetime
