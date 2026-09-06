from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import randbelow
from uuid import uuid4

from domainsmanager_application.auth import UnitOfWorkFactory
from domainsmanager_application.tasks import RefreshTaskRecord


@dataclass(frozen=True, slots=True)
class SchedulerPolicy:
    check_interval: timedelta = timedelta(days=7)
    batch_size: int = 100


class DomainSchedulerService:
    """Atomically reserve due domains and enqueue their refresh tasks."""

    def __init__(
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        policy: SchedulerPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        random_offset_seconds: Callable[[int], int] | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy or SchedulerPolicy()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._random_offset_seconds = random_offset_seconds or (
            lambda upper: randbelow(upper + 1)
        )

    async def spread_overdue(self) -> int:
        """Spread restart backlog across one full checking interval."""
        now = self._clock()
        total = 0
        while True:
            async with self._unit_of_work() as uow:
                count = await uow.domains.spread_due(
                    now,
                    self._policy.check_interval,
                    self._policy.batch_size,
                    self._random_offset_seconds,
                )
                if count:
                    await uow.commit()
            total += count
            if count < self._policy.batch_size:
                return total

    async def run_once(self) -> int:
        now = self._clock()
        next_check_at = now + self._policy.check_interval
        async with self._unit_of_work() as uow:
            domains = await uow.domains.claim_due(
                now, next_check_at, self._policy.batch_size
            )
            for domain in domains:
                task_id = uuid4()
                fingerprint = sha256(b"scheduled-domain-refresh").hexdigest()
                key = f"schedule:{domain.id}:{now.isoformat()}"
                await uow.tasks.add(
                    RefreshTaskRecord(
                        id=task_id,
                        user_id=domain.user_id,
                        domain_id=domain.id,
                        domain_name=domain.name_ascii,
                        status="queued",
                        force_refresh=False,
                        attempt_count=0,
                        created_at=now,
                        updated_at=now,
                        started_at=None,
                        completed_at=None,
                        check_id=None,
                        error_code=None,
                        error_message=None,
                        available_at=now,
                        origin="scheduled",
                    ),
                    key,
                    fingerprint,
                    now + self._policy.check_interval,
                )
            if domains:
                await uow.commit()
        return len(domains)
