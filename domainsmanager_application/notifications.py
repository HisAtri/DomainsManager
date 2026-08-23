from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from domainsmanager_application.auth import UnitOfWorkFactory
from domainsmanager_application.domains import DomainNotFoundError


class NotificationRuleError(RuntimeError):
    code = "notification_rule_error"


class NotificationRuleNotFoundError(NotificationRuleError):
    code = "not_found"


@dataclass(frozen=True, slots=True)
class NotificationRuleRecord:
    id: UUID
    user_id: UUID
    domain_id: UUID | None
    event_type: str
    days_before: int | None
    channel: str
    channel_config: dict
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class NotificationRuleRepository(Protocol):
    async def list(self, user_id: UUID, domain_id: UUID | None) -> list[NotificationRuleRecord]: ...
    async def add(self, record: NotificationRuleRecord) -> None: ...


class NotificationRuleService:
    def __init__(self, *, unit_of_work: UnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    async def list(self, user_id: UUID, domain_id: UUID | None = None) -> list[NotificationRuleRecord]:
        async with self._unit_of_work() as uow:
            if domain_id is not None and await uow.domains.get(user_id, domain_id) is None:
                raise DomainNotFoundError("domain was not found")
            return await uow.notifications.list(user_id, domain_id)

    async def create(self, user_id: UUID, *, domain_id: UUID | None, event_type: str, days_before: int | None, channel: str, channel_config: dict) -> NotificationRuleRecord:
        now = datetime.now(UTC)
        record = NotificationRuleRecord(uuid4(), user_id, domain_id, event_type, days_before, channel, channel_config, True, now, now)
        async with self._unit_of_work() as uow:
            if domain_id is not None and await uow.domains.get(user_id, domain_id) is None:
                raise DomainNotFoundError("domain was not found")
            await uow.notifications.add(record)
            await uow.commit()
        return record
