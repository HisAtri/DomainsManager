from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    id: UUID
    lease_token: UUID
    channel: str
    channel_config: dict
    payload: dict
    attempt_count: int
    recipient_email: str | None


class NotificationOutboxService:
    def __init__(self, *, unit_of_work: UnitOfWorkFactory, deliver: Callable[[OutboxMessage], Awaitable[None]], clock: Callable[[], datetime] | None = None, lease_duration: timedelta = timedelta(minutes=2), max_attempts: int = 5) -> None:
        self._unit_of_work, self._deliver, self._clock = unit_of_work, deliver, clock or (lambda: datetime.now(UTC))
        self._lease_duration, self._max_attempts = lease_duration, max_attempts

    async def run_once(self, worker_id: str) -> bool:
        now = self._clock()
        async with self._unit_of_work() as uow:
            message = await uow.notifications.claim_outbox(worker_id, now, now + self._lease_duration)
            if message is not None:
                await uow.commit()
        if message is None:
            return False
        try:
            await self._deliver(message)
        except Exception as error:  # noqa: BLE001 - adapters may raise transport errors
            async with self._unit_of_work() as uow:
                await uow.notifications.fail_outbox(message.id, message.lease_token, self._clock(), str(error), self._max_attempts)
                await uow.commit()
        else:
            async with self._unit_of_work() as uow:
                await uow.notifications.complete_outbox(message.id, message.lease_token, self._clock())
                await uow.commit()
        return True


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
