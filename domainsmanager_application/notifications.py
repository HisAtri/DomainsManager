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


class NotificationDeliverySuppressed(RuntimeError):
    """A notification was intentionally not sent by the delivery policy."""


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
    deleted_at: datetime | None = None


class NotificationRuleRepository(Protocol):
    async def list(self, user_id: UUID, domain_id: UUID | None) -> list[NotificationRuleRecord]: ...
    async def get(self, user_id: UUID, rule_id: UUID) -> NotificationRuleRecord | None: ...
    async def add(self, record: NotificationRuleRecord) -> None: ...
    async def update(self, record: NotificationRuleRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    id: UUID
    lease_token: UUID
    channel: str
    channel_config: dict
    payload: dict
    attempt_count: int
    recipient_email: str | None


@dataclass(frozen=True, slots=True)
class NotificationDeliveryRecord:
    id: UUID
    domain_id: UUID
    event_type: str
    channel: str
    status: str
    attempt_count: int
    available_at: datetime | None
    sent_at: datetime | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class NotificationOutboxRepository(Protocol):
    async def list_deliveries(
        self, user_id: UUID, limit: int
    ) -> list[NotificationDeliveryRecord]: ...

    async def suppress_outbox(
        self, message_id: UUID, token: UUID, at: datetime, reason: str
    ) -> bool: ...


class NotificationOutboxService:
    def __init__(self, *, unit_of_work: UnitOfWorkFactory, deliver: Callable[[OutboxMessage], Awaitable[None]], clock: Callable[[], datetime] | None = None, lease_duration: timedelta = timedelta(minutes=2), max_attempts: int = 5, retry_base_delay: timedelta = timedelta(minutes=1), retry_max_delay: timedelta = timedelta(hours=1)) -> None:
        self._unit_of_work, self._deliver, self._clock = unit_of_work, deliver, clock or (lambda: datetime.now(UTC))
        self._lease_duration, self._max_attempts = lease_duration, max_attempts
        self._retry_base_delay, self._retry_max_delay = retry_base_delay, retry_max_delay

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
        except NotificationDeliverySuppressed as error:
            async with self._unit_of_work() as uow:
                await uow.notifications.suppress_outbox(
                    message.id, message.lease_token, self._clock(), str(error)
                )
                await uow.commit()
        except Exception as error:  # noqa: BLE001 - adapters may raise transport errors
            async with self._unit_of_work() as uow:
                await uow.notifications.fail_outbox(message.id, message.lease_token, self._clock(), f"{type(error).__name__}: delivery failed", self._max_attempts, self._retry_delay(message.attempt_count))
                await uow.commit()
        else:
            async with self._unit_of_work() as uow:
                await uow.notifications.complete_outbox(message.id, message.lease_token, self._clock())
                await uow.commit()
        return True

    def _retry_delay(self, attempt_count: int) -> timedelta:
        return min(
            self._retry_base_delay * (2 ** max(attempt_count - 1, 0)),
            self._retry_max_delay,
        )


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

    async def get(self, user_id: UUID, rule_id: UUID) -> NotificationRuleRecord:
        async with self._unit_of_work() as uow:
            rule = await uow.notifications.get(user_id, rule_id)
        if rule is None:
            raise NotificationRuleNotFoundError("notification rule was not found")
        return rule

    async def update(
        self,
        user_id: UUID,
        rule_id: UUID,
        *,
        domain_id: UUID | None,
        event_type: str,
        days_before: int | None,
        channel: str,
        channel_config: dict,
        is_enabled: bool,
    ) -> NotificationRuleRecord:
        now = datetime.now(UTC)
        async with self._unit_of_work() as uow:
            existing = await uow.notifications.get(user_id, rule_id)
            if existing is None:
                raise NotificationRuleNotFoundError("notification rule was not found")
            if domain_id is not None and await uow.domains.get(user_id, domain_id) is None:
                raise DomainNotFoundError("domain was not found")
            updated = NotificationRuleRecord(rule_id, user_id, domain_id, event_type, days_before, channel, channel_config, is_enabled, existing.created_at, now)
            await uow.notifications.update(updated)
            await uow.commit()
        return updated

    async def delete(self, user_id: UUID, rule_id: UUID) -> None:
        existing = await self.get(user_id, rule_id)
        deleted = NotificationRuleRecord(existing.id, existing.user_id, existing.domain_id, existing.event_type, existing.days_before, existing.channel, existing.channel_config, False, existing.created_at, datetime.now(UTC), datetime.now(UTC))
        async with self._unit_of_work() as uow:
            await uow.notifications.update(deleted)
            await uow.commit()

    async def list_deliveries(
        self, user_id: UUID, *, limit: int = 50
    ) -> list[NotificationDeliveryRecord]:
        async with self._unit_of_work() as uow:
            return await uow.notifications.list_deliveries(user_id, limit)
