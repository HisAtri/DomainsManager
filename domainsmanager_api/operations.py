"""Collect and emit safe, aggregate operational signals."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from domainsmanager_persistence.models import (
    DomainRefreshTask,
    ManagedDomain,
    NotificationOutbox,
)


@dataclass(frozen=True, slots=True)
class OperationalMetrics:
    generated_at: datetime
    refresh_tasks_queued: int
    refresh_tasks_running: int
    refresh_tasks_expired_leases: int
    notification_outbox_pending: int
    notification_outbox_running: int
    notification_outbox_dead_letter: int
    notification_outbox_expired_leases: int
    overdue_monitored_domains: int

    def payload(self) -> dict[str, str | int]:
        payload = asdict(self)
        payload["generated_at"] = self.generated_at.isoformat()
        return payload


async def collect_operational_metrics(
    session: AsyncSession, *, now: datetime | None = None
) -> OperationalMetrics:
    generated_at = now or datetime.now(UTC)

    async def count(model: type, *conditions) -> int:
        return (
            await session.scalar(
                select(func.count()).select_from(model).where(*conditions)
            )
            or 0
        )

    return OperationalMetrics(
        generated_at=generated_at,
        refresh_tasks_queued=await count(
            DomainRefreshTask, DomainRefreshTask.status == "queued"
        ),
        refresh_tasks_running=await count(
            DomainRefreshTask, DomainRefreshTask.status == "running"
        ),
        refresh_tasks_expired_leases=await count(
            DomainRefreshTask,
            DomainRefreshTask.status == "running",
            DomainRefreshTask.lease_until.is_not(None),
            DomainRefreshTask.lease_until <= generated_at,
        ),
        notification_outbox_pending=await count(
            NotificationOutbox, NotificationOutbox.status == "pending"
        ),
        notification_outbox_running=await count(
            NotificationOutbox, NotificationOutbox.status == "running"
        ),
        notification_outbox_dead_letter=await count(
            NotificationOutbox, NotificationOutbox.status == "dead_letter"
        ),
        notification_outbox_expired_leases=await count(
            NotificationOutbox,
            NotificationOutbox.status == "running",
            NotificationOutbox.lease_until.is_not(None),
            NotificationOutbox.lease_until <= generated_at,
        ),
        overdue_monitored_domains=await count(
            ManagedDomain,
            ManagedDomain.monitor_enabled.is_(True),
            ManagedDomain.deleted_at.is_(None),
            ManagedDomain.next_check_at.is_not(None),
            ManagedDomain.next_check_at <= generated_at,
        ),
    )


def alert_events(metrics: OperationalMetrics) -> Iterator[dict[str, str | int]]:
    alerts = {
        "refresh_task_expired_lease": metrics.refresh_tasks_expired_leases,
        "notification_dead_letter": metrics.notification_outbox_dead_letter,
        "notification_expired_lease": metrics.notification_outbox_expired_leases,
        "overdue_monitored_domain": metrics.overdue_monitored_domains,
    }
    for alert, count in alerts.items():
        if count:
            yield {
                "event": "operational_alert",
                "alert": alert,
                "count": count,
                "generated_at": metrics.generated_at.isoformat(),
            }


def emit_operational_events(
    metrics: OperationalMetrics, logger: logging.Logger
) -> None:
    logger.info(
        json.dumps(
            {"event": "operational_metrics", **metrics.payload()}, separators=(",", ":")
        )
    )
    for event in alert_events(metrics):
        logger.warning(json.dumps(event, separators=(",", ":")))
