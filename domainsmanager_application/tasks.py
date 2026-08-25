from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from time import monotonic
from uuid import UUID, uuid4

from domainsmanager_application.auth import UnitOfWorkFactory
from domainsmanager_application.domains import DomainNotFoundError
from domainsmanager_lookup import DomainLookup, LookupOptions


class TaskError(RuntimeError):
    code = "task_error"


class TaskNotFoundError(TaskError):
    code = "not_found"


class IdempotencyConflictError(TaskError):
    code = "idempotency_conflict"


@dataclass(frozen=True, slots=True)
class TaskExecutionPolicy:
    lease_duration: timedelta = timedelta(minutes=2)
    successful_check_interval: timedelta = timedelta(days=1)
    successful_refresh_ttl: timedelta = timedelta(minutes=30)
    max_attempts: int = 5
    retry_base_delay: timedelta = timedelta(minutes=1)
    retry_max_delay: timedelta = timedelta(hours=1)

    def retry_at(self, now: datetime, attempt_count: int) -> datetime:
        delay = min(
            self.retry_base_delay * (2 ** max(attempt_count - 1, 0)),
            self.retry_max_delay,
        )
        return now + delay

    @property
    def heartbeat_interval(self) -> timedelta:
        """Renew a lease before it can be reclaimed by another worker."""
        return self.lease_duration / 3


@dataclass(frozen=True, slots=True)
class RefreshTaskRecord:
    id: UUID
    user_id: UUID
    domain_id: UUID
    domain_name: str
    status: str
    force_refresh: bool
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    check_id: UUID | None
    error_code: str | None
    error_message: str | None
    available_at: datetime | None = None
    max_attempts: int = 5
    lease_token: UUID | None = None
    result_code: str | None = None
    result_message: str | None = None
    source_check_id: UUID | None = None
    fresh_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class DomainCheckRecord:
    id: UUID
    domain_id: UUID
    checked_at: datetime
    outcome: str
    duration_ms: int | None
    error_code: str | None
    error_message: str | None
    protocol: str | None
    source: str | None
    snapshot: dict | None
    changed_fields: list[str]
    is_stale: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CheckPage:
    items: list[DomainCheckRecord]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class TaskPage:
    items: list[RefreshTaskRecord]
    total: int
    page: int
    page_size: int


class RefreshTaskService:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        lookup: DomainLookup,
        clock: Callable[[], datetime] | None = None,
        policy: TaskExecutionPolicy | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._lookup = lookup
        self._clock = clock or (lambda: datetime.now(UTC))
        self._policy = policy or TaskExecutionPolicy()

    async def enqueue(
        self,
        user_id: UUID,
        domain_id: UUID,
        *,
        force_refresh: bool,
        idempotency_key: str,
    ) -> RefreshTaskRecord:
        now = self._clock()
        fingerprint = sha256(str(force_refresh).encode()).hexdigest()
        async with self._unit_of_work() as uow:
            domain = await uow.domains.get(user_id, domain_id)
            if domain is None:
                raise DomainNotFoundError("domain was not found")
            existing = await uow.tasks.get_idempotent(
                user_id, "domain_refresh", domain_id, idempotency_key
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise IdempotencyConflictError(
                        "idempotency key has a different request"
                    )
                task = await uow.tasks.get(user_id, existing.task_id)
                if task is None:
                    raise TaskNotFoundError("task was not found")
                return task
            task = RefreshTaskRecord(
                id=uuid4(),
                user_id=user_id,
                domain_id=domain_id,
                domain_name=domain.name_ascii,
                status="queued",
                force_refresh=force_refresh,
                attempt_count=0,
                created_at=now,
                updated_at=now,
                started_at=None,
                completed_at=None,
                check_id=None,
                error_code=None,
                error_message=None,
                available_at=now,
                max_attempts=self._policy.max_attempts,
            )
            await uow.tasks.add(
                task, idempotency_key, fingerprint, now + timedelta(days=1)
            )
            await uow.commit()
            return task

    async def get(self, user_id: UUID, task_id: UUID) -> RefreshTaskRecord:
        async with self._unit_of_work() as uow:
            task = await uow.tasks.get(user_id, task_id)
        if task is None:
            raise TaskNotFoundError("task was not found")
        return task

    async def list(
        self, user_id: UUID, *, page: int, page_size: int, status: str | None
    ) -> TaskPage:
        async with self._unit_of_work() as uow:
            return await uow.tasks.list(user_id, page, page_size, status)

    async def enqueue_as_admin(
        self,
        domain_id: UUID,
        *,
        force_refresh: bool,
        idempotency_key: str,
    ) -> RefreshTaskRecord:
        async with self._unit_of_work() as uow:
            domain = await uow.domains.get_any(domain_id)
        if domain is None:
            raise DomainNotFoundError("domain was not found")
        return await self.enqueue(
            domain.user_id,
            domain_id,
            force_refresh=force_refresh,
            idempotency_key=idempotency_key,
        )

    async def list_checks(
        self,
        user_id: UUID,
        domain_id: UUID,
        *,
        page: int,
        page_size: int,
        outcome: str | None,
        checked_from: datetime | None,
        checked_to: datetime | None,
    ) -> CheckPage:
        async with self._unit_of_work() as uow:
            if await uow.domains.get(user_id, domain_id) is None:
                raise DomainNotFoundError("domain was not found")
            return await uow.tasks.list_checks(
                domain_id, page, page_size, outcome, checked_from, checked_to
            )

    async def get_check(
        self, user_id: UUID, domain_id: UUID, check_id: UUID
    ) -> DomainCheckRecord:
        async with self._unit_of_work() as uow:
            if await uow.domains.get(user_id, domain_id) is None:
                raise DomainNotFoundError("domain was not found")
            check = await uow.tasks.get_check(domain_id, check_id)
        if check is None:
            raise TaskNotFoundError("check was not found")
        return check

    async def run_once(self, worker_id: str) -> bool:
        now = self._clock()
        async with self._unit_of_work() as uow:
            task = await uow.tasks.claim(
                worker_id, now, now + self._policy.lease_duration
            )
            if task is not None:
                await uow.commit()
        if task is None:
            return False
        completed = self._clock()
        async with self._unit_of_work() as uow:
            if await uow.tasks.complete_if_fresh(
                task.id,
                task.lease_token,
                completed,
                fresh_after=completed - self._policy.successful_refresh_ttl,
                fresh_until=completed + self._policy.successful_refresh_ttl,
                result_message=self._fresh_message(self._policy.successful_refresh_ttl),
            ):
                await uow.commit()
                return True
        started = monotonic()
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._maintain_lease(task, stop_heartbeat), name=f"task-lease-{task.id}"
        )
        try:
            outcome = await self._lookup.lookup(
                [task.domain_name],
                options=LookupOptions(force_refresh=task.force_refresh),
            )
        finally:
            stop_heartbeat.set()
            await heartbeat
        result = outcome[0]
        completed = self._clock()
        duration_ms = int((monotonic() - started) * 1000)
        async with self._unit_of_work() as uow:
            if result.succeeded:
                snapshot = result.snapshot.model_dump(mode="json")
                check = await uow.tasks.complete_success(
                    task.id,
                    task.lease_token,
                    completed,
                    duration_ms,
                    snapshot,
                    next_check_at=completed + self._policy.successful_check_interval,
                )
            else:
                retry_at = (
                    self._policy.retry_at(completed, task.attempt_count)
                    if self._is_retryable(result.error_code)
                    and task.attempt_count < task.max_attempts
                    else None
                )
                check = await uow.tasks.complete_failure(
                    task.id,
                    task.lease_token,
                    completed,
                    duration_ms,
                    result.error_code.value
                    if result.error_code
                    else "unexpected_response",
                    result.error_message or "lookup failed",
                    retry_at=retry_at,
                )
            if check is not None:
                await uow.commit()
        return True

    async def _maintain_lease(
        self, task: RefreshTaskRecord, stop: asyncio.Event
    ) -> None:
        """Periodically extend the claimed task lease while lookup is in flight."""
        interval = self._policy.heartbeat_interval.total_seconds()
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                now = self._clock()
                async with self._unit_of_work() as uow:
                    renewed = await uow.tasks.heartbeat(
                        task.id,
                        task.lease_token,
                        now + self._policy.lease_duration,
                        now,
                    )
                    if not renewed:
                        return
                    await uow.commit()

    @staticmethod
    def _is_retryable(error_code: object) -> bool:
        value = getattr(error_code, "value", error_code)
        return value in {"rate_limited", "temporary_failure"}

    @staticmethod
    def _fresh_message(interval: timedelta) -> str:
        seconds = int(interval.total_seconds())
        if seconds % 86_400 == 0:
            value = f"{seconds // 86_400}天"
        elif seconds % 3_600 == 0:
            value = f"{seconds // 3_600}小时"
        elif seconds % 60 == 0:
            value = f"{seconds // 60}分钟"
        else:
            value = f"{seconds}秒"
        return f"距上次成功刷新不足{value}"
