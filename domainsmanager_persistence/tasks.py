from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from domainsmanager_application.tasks import (
    CheckPage,
    DomainCheckRecord,
    RefreshTaskRecord,
)
from domainsmanager_persistence.models import (
    DomainCheck,
    DomainRefreshTask,
    IdempotencyRecord,
    ManagedDomain,
)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class SqlAlchemyTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_idempotent(
        self, user_id: UUID, operation: str, resource_id: UUID, key: str
    ) -> IdempotencyRecord | None:
        result = await self._session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user_id == user_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.resource_id == resource_id,
                IdempotencyRecord.key == key,
            )
        )
        return result.scalar_one_or_none()

    async def add(
        self,
        task: RefreshTaskRecord,
        key: str,
        fingerprint: str,
        expires_at: datetime,
    ) -> None:
        self._session.add(
            DomainRefreshTask(
                id=task.id,
                user_id=task.user_id,
                managed_domain_id=task.domain_id,
                status=task.status,
                force_refresh=task.force_refresh,
                attempt_count=task.attempt_count,
                max_attempts=task.max_attempts,
                available_at=task.created_at,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
        )
        self._session.add(
            IdempotencyRecord(
                id=uuid4(),
                user_id=task.user_id,
                operation="domain_refresh",
                resource_id=task.domain_id,
                key=key,
                request_fingerprint=fingerprint,
                task_id=task.id,
                created_at=task.created_at,
                expires_at=expires_at,
            )
        )
        await self._session.flush()

    async def get(self, user_id: UUID, task_id: UUID) -> RefreshTaskRecord | None:
        result = await self._session.execute(
            select(DomainRefreshTask, ManagedDomain.name_ascii)
            .join(
                ManagedDomain, ManagedDomain.id == DomainRefreshTask.managed_domain_id
            )
            .where(
                DomainRefreshTask.id == task_id, DomainRefreshTask.user_id == user_id
            )
        )
        row = result.one_or_none()
        return self._task_record(*row) if row is not None else None

    async def claim(
        self, worker_id: str, now: datetime, lease_until: datetime
    ) -> RefreshTaskRecord | None:
        claimable = or_(
            and_(
                DomainRefreshTask.status == "queued",
                DomainRefreshTask.available_at <= now,
            ),
            and_(
                DomainRefreshTask.status == "running",
                DomainRefreshTask.lease_until.is_not(None),
                DomainRefreshTask.lease_until <= now,
            ),
        )
        statement = (
            select(DomainRefreshTask, ManagedDomain.name_ascii)
            .join(
                ManagedDomain, ManagedDomain.id == DomainRefreshTask.managed_domain_id
            )
            .where(claimable)
            .order_by(DomainRefreshTask.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        task, domain_name = row
        token = uuid4()
        task.status = "running"
        task.lease_token = token
        task.lease_owner = worker_id
        task.lease_until = lease_until
        task.started_at = task.started_at or now
        task.updated_at = now
        task.attempt_count += 1
        await self._session.flush()
        return self._task_record(task, domain_name)

    async def heartbeat(
        self, task_id: UUID, lease_token: UUID, lease_until: datetime, at: datetime
    ) -> bool:
        task = await self._locked_task(task_id, lease_token)
        if task is None:
            return False
        task.lease_until = lease_until
        task.updated_at = at
        await self._session.flush()
        return True

    async def complete_success(
        self,
        task_id: UUID,
        lease_token: UUID | None,
        at: datetime,
        duration_ms: int,
        snapshot: dict,
    ) -> DomainCheckRecord | None:
        task = await self._locked_task(task_id, lease_token)
        if task is None:
            return None
        check = DomainCheck(
            id=uuid4(),
            managed_domain_id=task.managed_domain_id,
            checked_at=at,
            duration_ms=duration_ms,
            outcome="success",
            protocol=snapshot.get("source"),
            source=snapshot.get("source_url"),
            snapshot=snapshot,
            changed_fields=[],
            is_stale=False,
            created_at=at,
        )
        self._session.add(check)
        domain = await self._session.get(ManagedDomain, task.managed_domain_id)
        if domain is not None:
            domain.registrar_json = snapshot.get("registrar")
            domain.statuses = snapshot.get("statuses", [])
            domain.registered_at = snapshot.get("registered_at")
            domain.expires_at = snapshot.get("expires_at")
            domain.registry_updated_at = snapshot.get("updated_at")
            domain.nameservers = snapshot.get("nameservers", [])
            domain.dnssec_enabled = snapshot.get("dnssec_enabled")
            domain.latest_source = snapshot.get("source")
            domain.last_successful_check_at = at
            domain.last_check_at = at
            domain.last_outcome = "success"
            domain.updated_at = at
            domain.version += 1
        task.status = "succeeded"
        task.domain_check_id = check.id
        task.completed_at = at
        task.lease_token = None
        task.lease_owner = None
        task.lease_until = None
        task.updated_at = at
        await self._session.flush()
        return self._check_record(check)

    async def complete_failure(
        self,
        task_id: UUID,
        lease_token: UUID | None,
        at: datetime,
        duration_ms: int,
        error_code: str,
        error_message: str,
        *,
        retry_at: datetime | None = None,
    ) -> DomainCheckRecord | None:
        task = await self._locked_task(task_id, lease_token)
        if task is None:
            return None
        check = DomainCheck(
            id=uuid4(),
            managed_domain_id=task.managed_domain_id,
            checked_at=at,
            duration_ms=duration_ms,
            outcome=error_code,
            error_code=error_code,
            error_message=error_message[:512],
            changed_fields=[],
            is_stale=False,
            created_at=at,
        )
        self._session.add(check)
        domain = await self._session.get(ManagedDomain, task.managed_domain_id)
        if domain is not None:
            domain.last_check_at = at
            domain.last_outcome = error_code
            domain.updated_at = at
            domain.version += 1
        task.status = "queued" if retry_at is not None else "failed"
        task.domain_check_id = check.id
        task.error_code = error_code
        task.error_message = error_message[:512]
        task.completed_at = at if retry_at is None else None
        task.available_at = retry_at or task.available_at
        task.lease_token = None
        task.lease_owner = None
        task.lease_until = None
        task.updated_at = at
        await self._session.flush()
        return self._check_record(check)

    async def list_checks(
        self,
        domain_id: UUID,
        page: int,
        page_size: int,
        outcome: str | None,
        checked_from: datetime | None,
        checked_to: datetime | None,
    ) -> CheckPage:
        filters = [DomainCheck.managed_domain_id == domain_id]
        if outcome is not None:
            filters.append(DomainCheck.outcome == outcome)
        if checked_from is not None:
            filters.append(DomainCheck.checked_at >= checked_from)
        if checked_to is not None:
            filters.append(DomainCheck.checked_at <= checked_to)
        total = await self._session.scalar(
            select(func.count()).select_from(DomainCheck).where(*filters)
        )
        rows = (
            (
                await self._session.execute(
                    select(DomainCheck)
                    .where(*filters)
                    .order_by(DomainCheck.checked_at.desc(), DomainCheck.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        return CheckPage(
            items=[self._check_record(row) for row in rows],
            total=total or 0,
            page=page,
            page_size=page_size,
        )

    async def get_check(
        self, domain_id: UUID, check_id: UUID
    ) -> DomainCheckRecord | None:
        result = await self._session.execute(
            select(DomainCheck).where(
                DomainCheck.id == check_id, DomainCheck.managed_domain_id == domain_id
            )
        )
        row = result.scalar_one_or_none()
        return self._check_record(row) if row is not None else None

    async def _locked_task(
        self, task_id: UUID, lease_token: UUID | None
    ) -> DomainRefreshTask | None:
        result = await self._session.execute(
            select(DomainRefreshTask)
            .where(
                DomainRefreshTask.id == task_id,
                DomainRefreshTask.status == "running",
                DomainRefreshTask.lease_token == lease_token,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _task_record(task: DomainRefreshTask, domain_name: str) -> RefreshTaskRecord:
        return RefreshTaskRecord(
            id=task.id,
            user_id=task.user_id,
            domain_id=task.managed_domain_id,
            domain_name=domain_name,
            status=task.status,
            force_refresh=task.force_refresh,
            attempt_count=task.attempt_count,
            created_at=as_utc(task.created_at),
            updated_at=as_utc(task.updated_at),
            started_at=as_utc(task.started_at),
            completed_at=as_utc(task.completed_at),
            check_id=task.domain_check_id,
            error_code=task.error_code,
            error_message=task.error_message,
            available_at=as_utc(task.available_at),
            max_attempts=task.max_attempts,
            lease_token=task.lease_token,
        )

    @staticmethod
    def _check_record(check: DomainCheck) -> DomainCheckRecord:
        return DomainCheckRecord(
            id=check.id,
            domain_id=check.managed_domain_id,
            checked_at=as_utc(check.checked_at),
            outcome=check.outcome,
            duration_ms=check.duration_ms,
            error_code=check.error_code,
            error_message=check.error_message,
            protocol=check.protocol,
            source=check.source,
            snapshot=dict(check.snapshot) if check.snapshot is not None else None,
            changed_fields=list(check.changed_fields),
            is_stale=check.is_stale,
            created_at=as_utc(check.created_at),
        )
