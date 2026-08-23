from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domainsmanager_application.notifications import NotificationRuleRecord
from domainsmanager_persistence.models import NotificationRule


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

    @staticmethod
    def _record(row: NotificationRule) -> NotificationRuleRecord:
        return NotificationRuleRecord(row.id, row.user_id, row.managed_domain_id, row.event_type, row.days_before, row.channel, dict(row.channel_config), row.is_enabled, as_utc(row.created_at), as_utc(row.updated_at))
