from fastapi import APIRouter, HTTPException, Query, status

from domainsmanager_api.dependencies import (
    CurrentUserDependency,
    NotificationServiceDependency,
)
from domainsmanager_api.schemas.notifications import (
    CreateNotificationRuleRequest,
    NotificationDeliveryResponse,
    NotificationRuleResponse,
)
from domainsmanager_application.domains import DomainError
from domainsmanager_application.notifications import (
    NotificationDeliveryRecord,
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


@router.get("", response_model=list[NotificationRuleResponse])
async def list_rules(current: CurrentUserDependency, notifications: NotificationServiceDependency) -> list[NotificationRuleResponse]:
    return [response(item) for item in await notifications.list(current.user.id)]


@router.post("", response_model=NotificationRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(body: CreateNotificationRuleRequest, current: CurrentUserDependency, notifications: NotificationServiceDependency) -> NotificationRuleResponse:
    if body.event_type == "expiration" and body.days_before is None:
        raise HTTPException(status_code=422, detail={"code": "validation_error", "message": "days_before is required for expiration rules"})
    if body.channel == "webhook" and body.webhook_url is None:
        raise HTTPException(status_code=422, detail={"code": "validation_error", "message": "webhook_url is required for webhook rules"})
    if body.channel == "email" and body.webhook_url is not None:
        raise HTTPException(status_code=422, detail={"code": "validation_error", "message": "webhook_url is only valid for webhook rules"})
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
    return [
        delivery_response(item)
        for item in await notifications.list_deliveries(current.user.id, limit=limit)
    ]
