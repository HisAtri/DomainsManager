from __future__ import annotations

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
    lease_token: UUID | None = None


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


class RefreshTaskService:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        lookup: DomainLookup,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._lookup = lookup
        self._clock = clock or (lambda: datetime.now(UTC))

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
            task = await uow.tasks.claim(worker_id, now, now + timedelta(minutes=2))
            if task is not None:
                await uow.commit()
        if task is None:
            return False
        started = monotonic()
        outcome = await self._lookup.lookup(
            [task.domain_name], options=LookupOptions(force_refresh=task.force_refresh)
        )
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
                )
            else:
                check = await uow.tasks.complete_failure(
                    task.id,
                    task.lease_token,
                    completed,
                    duration_ms,
                    result.error_code.value
                    if result.error_code
                    else "unexpected_response",
                    result.error_message or "lookup failed",
                )
            if check is not None:
                await uow.commit()
        return True
