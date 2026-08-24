from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class CreateNotificationRuleRequest(BaseModel):
    domain_id: UUID | None = None
    event_type: Literal["expiration", "status_change", "query_failure"]
    days_before: int | None = Field(default=None, ge=0, le=365)
    channel: Literal["email", "webhook"]
    webhook_url: HttpUrl | None = None


class UpdateNotificationRuleRequest(BaseModel):
    domain_id: UUID | None = None
    event_type: Literal["expiration", "status_change", "query_failure"] | None = None
    days_before: int | None = Field(default=None, ge=0, le=365)
    channel: Literal["email", "webhook"] | None = None
    webhook_url: HttpUrl | None = None
    enabled: bool | None = None


class NotificationRuleResponse(BaseModel):
    id: UUID
    domain_id: UUID | None
    event_type: str
    days_before: int | None
    channel: str
    webhook_url: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class NotificationDeliveryResponse(BaseModel):
    id: UUID
    domain_id: UUID
    event_type: str
    channel: str
    status: Literal["pending", "running", "sent", "dead_letter"]
    attempt_count: int
    available_at: datetime | None
    sent_at: datetime | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime
