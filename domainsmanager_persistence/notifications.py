from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from domainsmanager_application.notifications import (
    NotificationDeliveryRecord,
    NotificationRuleRecord,
    OutboxMessage,
)
from domainsmanager_persistence.models import (
    AppUser,
    NotificationOutbox,
    NotificationRule,
)


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SqlAlchemyNotificationRuleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, user_id: UUID, domain_id: UUID | None) -> list[NotificationRuleRecord]:
        statement = select(NotificationRule).where(NotificationRule.user_id == user_id)
        if domain_id is not None:
            statement = statement.where(NotificationRule.managed_domain_id == domain_id)
        rows = (await self._session.execute(statement.order_by(NotificationRule.created_at))).scalars().all()
        return [self._record(row) for row in rows]

    async def add(self, record: NotificationRuleRecord) -> None:
        self._session.add(NotificationRule(id=record.id, user_id=record.user_id, managed_domain_id=record.domain_id, event_type=record.event_type, days_before=record.days_before, channel=record.channel, channel_config=dict(record.channel_config), is_enabled=record.is_enabled, created_at=record.created_at, updated_at=record.updated_at))
        await self._session.flush()

    async def list_deliveries(
        self, user_id: UUID, limit: int
    ) -> list[NotificationDeliveryRecord]:
        rows = (
            await self._session.execute(
                select(NotificationOutbox, NotificationRule.channel)
                .join(
                    NotificationRule,
                    NotificationRule.id == NotificationOutbox.notification_rule_id,
                )
                .where(NotificationRule.user_id == user_id)
                .order_by(NotificationOutbox.created_at.desc())
                .limit(limit)
            )
        ).all()
        return [
            NotificationDeliveryRecord(
                id=outbox.id,
                domain_id=outbox.managed_domain_id,
                event_type=outbox.event_type,
                channel=channel,
                status=outbox.status,
                attempt_count=outbox.attempt_count,
                available_at=(as_utc(outbox.available_at) if outbox.available_at else None),
                sent_at=as_utc(outbox.sent_at) if outbox.sent_at else None,
                failure_reason=outbox.last_error,
                created_at=as_utc(outbox.created_at),
                updated_at=as_utc(outbox.updated_at),
            )
            for outbox, channel in rows
        ]

    async def claim_outbox(self, worker_id: str, now: datetime, lease_until: datetime) -> OutboxMessage | None:
        row = (await self._session.execute(select(NotificationOutbox, NotificationRule, AppUser.email).select_from(NotificationOutbox).join(NotificationRule, NotificationRule.id == NotificationOutbox.notification_rule_id).join(AppUser, AppUser.id == NotificationRule.user_id).where(or_(and_(NotificationOutbox.status == "pending", NotificationOutbox.available_at <= now), and_(NotificationOutbox.status == "running", NotificationOutbox.lease_until <= now))).order_by(NotificationOutbox.available_at, NotificationOutbox.id).limit(1).with_for_update(skip_locked=True))).one_or_none()
        if row is None:
            return None
        outbox, rule, email = row
        token = uuid4()
        outbox.status, outbox.lease_token, outbox.lease_owner, outbox.lease_until, outbox.updated_at = "running", token, worker_id, lease_until, now
        outbox.attempt_count += 1
        await self._session.flush()
        return OutboxMessage(outbox.id, token, rule.channel, dict(rule.channel_config), dict(outbox.payload), outbox.attempt_count, email)

    async def complete_outbox(self, message_id: UUID, token: UUID, at: datetime) -> bool:
        row = await self._locked_outbox(message_id, token)
        if row is None: return False
        row.status, row.sent_at, row.lease_token, row.lease_owner, row.lease_until, row.updated_at = "sent", at, None, None, None, at
        await self._session.flush(); return True

    async def fail_outbox(self, message_id: UUID, token: UUID, at: datetime, error: str, max_attempts: int, retry_delay: timedelta) -> bool:
        row = await self._locked_outbox(message_id, token)
        if row is None: return False
        row.status = "dead_letter" if row.attempt_count >= max_attempts else "pending"
        row.available_at = at if row.status == "dead_letter" else at + retry_delay
        row.last_error, row.lease_token, row.lease_owner, row.lease_until, row.updated_at = error[:512], None, None, None, at
        await self._session.flush(); return True

    async def _locked_outbox(self, message_id: UUID, token: UUID) -> NotificationOutbox | None:
        return (await self._session.execute(select(NotificationOutbox).where(NotificationOutbox.id == message_id, NotificationOutbox.status == "running", NotificationOutbox.lease_token == token).with_for_update())).scalar_one_or_none()

    @staticmethod
    def _record(row: NotificationRule) -> NotificationRuleRecord:
        return NotificationRuleRecord(row.id, row.user_id, row.managed_domain_id, row.event_type, row.days_before, row.channel, dict(row.channel_config), row.is_enabled, as_utc(row.created_at), as_utc(row.updated_at))
