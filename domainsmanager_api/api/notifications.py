from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from domainsmanager_api.dependencies import (
    CurrentUserDependency,
    NotificationServiceDependency,
)
from domainsmanager_api.schemas.notifications import (
    CreateNotificationRuleRequest,
    NotificationDeliveryResponse,
    NotificationRuleResponse,
    UpdateNotificationRuleRequest,
)
from domainsmanager_application.domains import DomainError
from domainsmanager_application.notifications import (
    NotificationDeliveryRecord,
    NotificationRuleNotFoundError,
    NotificationRuleRecord,
)

router = APIRouter(prefix="/notification-rules", tags=["Notifications"])


def response(record: NotificationRuleRecord) -> NotificationRuleResponse:
    return NotificationRuleResponse(id=record.id, domain_id=record.domain_id, event_type=record.event_type, days_before=record.days_before, channel=record.channel, webhook_url=record.channel_config.get("webhook_url"), enabled=record.is_enabled, created_at=record.created_at, updated_at=record.updated_at)


def delivery_response(record: NotificationDeliveryRecord) -> NotificationDeliveryResponse:
    return NotificationDeliveryResponse(
        id=record.id,
        domain_id=record.domain_id,
        event_type=record.event_type,
        channel=record.channel,
        status=record.status,
        attempt_count=record.attempt_count,
        available_at=record.available_at,
        sent_at=record.sent_at,
        failure_reason=record.failure_reason,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def validate_rule(event_type: str, days_before: int | None, channel: str, webhook_url: str | None) -> None:
    if event_type == "expiration" and days_before is None:
        raise HTTPException(status_code=422, detail={"code": "validation_error", "message": "days_before is required for expiration rules"})
    if event_type != "expiration" and days_before is not None:
        raise HTTPException(status_code=422, detail={"code": "validation_error", "message": "days_before is only valid for expiration rules"})
    if channel == "webhook" and webhook_url is None:
        raise HTTPException(status_code=422, detail={"code": "validation_error", "message": "webhook_url is required for webhook rules"})
    if channel == "email" and webhook_url is not None:
        raise HTTPException(status_code=422, detail={"code": "validation_error", "message": "webhook_url is only valid for webhook rules"})


@router.get("", response_model=list[NotificationRuleResponse])
async def list_rules(current: CurrentUserDependency, notifications: NotificationServiceDependency) -> list[NotificationRuleResponse]:
    return [response(item) for item in await notifications.list(current.user.id)]


@router.post("", response_model=NotificationRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(body: CreateNotificationRuleRequest, current: CurrentUserDependency, notifications: NotificationServiceDependency) -> NotificationRuleResponse:
    validate_rule(body.event_type, body.days_before, body.channel, str(body.webhook_url) if body.webhook_url else None)
    try:
        rule = await notifications.create(current.user.id, domain_id=body.domain_id, event_type=body.event_type, days_before=body.days_before, channel=body.channel, channel_config={"webhook_url": str(body.webhook_url)} if body.webhook_url else {})
    except DomainError as error:
        raise HTTPException(status_code=404, detail={"code": error.code, "message": str(error)}) from error
    return response(rule)


@router.get("/deliveries", response_model=list[NotificationDeliveryResponse])
async def list_deliveries(
    current: CurrentUserDependency,
    notifications: NotificationServiceDependency,
    limit: int = Query(default=50, ge=1, le=100),
) -> list[NotificationDeliveryResponse]:
    return [delivery_response(item) for item in await notifications.list_deliveries(current.user.id, limit=limit)]


@router.get("/{rule_id}", response_model=NotificationRuleResponse)
async def get_rule(rule_id: UUID, current: CurrentUserDependency, notifications: NotificationServiceDependency) -> NotificationRuleResponse:
    try:
        return response(await notifications.get(current.user.id, rule_id))
    except NotificationRuleNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": error.code, "message": str(error)}) from error


@router.patch("/{rule_id}", response_model=NotificationRuleResponse)
async def update_rule(rule_id: UUID, body: UpdateNotificationRuleRequest, current: CurrentUserDependency, notifications: NotificationServiceDependency) -> NotificationRuleResponse:
    try:
        existing = await notifications.get(current.user.id, rule_id)
        values = body.model_dump(exclude_unset=True)
        event_type = values.get("event_type", existing.event_type)
        channel = values.get("channel", existing.channel)
        days_before = values.get("days_before", existing.days_before)
        webhook_url = values.get("webhook_url", existing.channel_config.get("webhook_url"))
        validate_rule(event_type, days_before, channel, str(webhook_url) if webhook_url else None)
        rule = await notifications.update(current.user.id, rule_id, domain_id=values.get("domain_id", existing.domain_id), event_type=event_type, days_before=days_before, channel=channel, channel_config={"webhook_url": str(webhook_url)} if webhook_url else {}, is_enabled=values.get("enabled", existing.is_enabled))
    except NotificationRuleNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": error.code, "message": str(error)}) from error
    except DomainError as error:
        raise HTTPException(status_code=404, detail={"code": error.code, "message": str(error)}) from error
    return response(rule)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: UUID, current: CurrentUserDependency, notifications: NotificationServiceDependency) -> None:
    try:
        await notifications.delete(current.user.id, rule_id)
    except NotificationRuleNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": error.code, "message": str(error)}) from error


