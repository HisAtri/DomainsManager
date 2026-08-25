from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from domainsmanager_application.tasks import (
    CheckPage,
    DomainCheckRecord,
    RefreshTaskRecord,
    TaskPage,
)
from domainsmanager_persistence.models import (
    DomainCheck,
    DomainRefreshTask,
    IdempotencyRecord,
    GlobalSetting,
    ManagedDomain,
    NotificationOutbox,
    NotificationRule,
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
                available_at=task.available_at or task.created_at,
                created_at=task.created_at,
                updated_at=task.updated_at,
                result_code=task.result_code,
                result_message=task.result_message,
                source_check_id=task.source_check_id,
                fresh_until=task.fresh_until,
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

    async def list(
        self, user_id: UUID, page: int, page_size: int, status: str | None
    ) -> TaskPage:
        filters = [DomainRefreshTask.user_id == user_id]
        if status is not None:
            filters.append(DomainRefreshTask.status == status)
        total = await self._session.scalar(
            select(func.count()).select_from(DomainRefreshTask).where(*filters)
        )
        rows = (
            await self._session.execute(
                select(DomainRefreshTask, ManagedDomain.name_ascii)
                .join(ManagedDomain, ManagedDomain.id == DomainRefreshTask.managed_domain_id)
                .where(*filters)
                .order_by(DomainRefreshTask.created_at.desc(), DomainRefreshTask.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return TaskPage(
            items=[self._task_record(*row) for row in rows],
            total=total or 0,
            page=page,
            page_size=page_size,
        )

    async def get_global_setting(self, key: str) -> str | None:
        row = await self._session.get(GlobalSetting, key)
        return row.value if row is not None else None

    async def set_global_setting(self, key: str, value: str, at: datetime) -> None:
        row = await self._session.get(GlobalSetting, key)
        if row is None:
            self._session.add(GlobalSetting(key=key, value=value, updated_at=at))
        else:
            row.value = value
            row.updated_at = at
        await self._session.flush()

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
        *,
        next_check_at: datetime,
    ) -> DomainCheckRecord | None:
        task = await self._locked_task(task_id, lease_token)
        if task is None:
            return None
        previous_snapshot = await self._previous_success_snapshot(
            task.managed_domain_id
        )
        snapshot_hash = self._snapshot_hash(snapshot)
        check = DomainCheck(
            id=uuid4(),
            managed_domain_id=task.managed_domain_id,
            checked_at=at,
            duration_ms=duration_ms,
            outcome="success",
            protocol=snapshot.get("source"),
            source=snapshot.get("source_url"),
            snapshot=snapshot,
            snapshot_hash=snapshot_hash,
            changed_fields=self._changed_fields(previous_snapshot, snapshot),
            is_stale=False,
            created_at=at,
        )
        self._session.add(check)
        domain = await self._session.get(ManagedDomain, task.managed_domain_id)
        if domain is not None:
            domain.registrar_json = snapshot.get("registrar")
            domain.statuses = snapshot.get("statuses", [])
            domain.registered_at = self._snapshot_datetime(snapshot.get("registered_at"))
            domain.expires_at = self._snapshot_datetime(snapshot.get("expires_at"))
            domain.registry_updated_at = self._snapshot_datetime(snapshot.get("updated_at"))
            domain.nameservers = snapshot.get("nameservers", [])
            domain.dnssec_enabled = snapshot.get("dnssec_enabled")
            domain.latest_source = snapshot.get("source")
            domain.last_successful_check_at = at
            domain.last_check_at = at
            domain.last_outcome = "success"
            domain.next_check_at = next_check_at
            domain.updated_at = at
            domain.version += 1
        task.status = "success"
        task.domain_check_id = check.id
        task.result_code = "refreshed"
        task.result_message = None
        task.source_check_id = None
        task.fresh_until = None
        task.completed_at = at
        task.lease_token = None
        task.lease_owner = None
        task.lease_until = None
        task.updated_at = at
        await self._session.flush()
        if check.changed_fields:
            await self._queue_notifications(task, check, "status_change", at)
        await self._queue_expiration_notifications(task, check, at)
        return self._check_record(check)

    async def complete_if_fresh(
        self,
        task_id: UUID,
        lease_token: UUID | None,
        at: datetime,
        *,
        fresh_after: datetime,
        fresh_until: datetime,
        result_message: str,
    ) -> bool:
        task = await self._locked_task(task_id, lease_token)
        if task is None:
            return False
        latest = (
            await self._session.execute(
                select(DomainCheck.id, DomainCheck.checked_at)
                .where(
                    DomainCheck.managed_domain_id == task.managed_domain_id,
                    DomainCheck.outcome == "success",
                    DomainCheck.snapshot.is_not(None),
                    DomainCheck.checked_at >= fresh_after,
                )
                .order_by(DomainCheck.checked_at.desc(), DomainCheck.id.desc())
                .limit(1)
            )
        ).one_or_none()
        if latest is None:
            return False
        task.status = "info"
        task.domain_check_id = None
        task.source_check_id = latest.id
        task.result_code = "data_fresh"
        task.result_message = result_message[:512]
        task.fresh_until = fresh_until
        task.completed_at = at
        task.lease_token = None
        task.lease_owner = None
        task.lease_until = None
        task.updated_at = at
        await self._session.flush()
        return True

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
        task.result_code = "failed" if retry_at is None else None
        task.result_message = error_message[:512] if retry_at is None else None
        task.source_check_id = None
        task.fresh_until = None
        task.completed_at = at if retry_at is None else None
        task.available_at = retry_at or task.available_at
        task.lease_token = None
        task.lease_owner = None
        task.lease_until = None
        task.updated_at = at
        await self._session.flush()
        await self._queue_notifications(task, check, "query_failure", at)
        return self._check_record(check)

    async def _queue_notifications(
        self, task: DomainRefreshTask, check: DomainCheck, event_type: str, at: datetime
    ) -> None:
        rules = (await self._session.execute(
            select(NotificationRule).where(
                NotificationRule.user_id == task.user_id,
                NotificationRule.is_enabled.is_(True),
                NotificationRule.event_type == event_type,
                or_(NotificationRule.managed_domain_id.is_(None), NotificationRule.managed_domain_id == task.managed_domain_id),
            )
        )).scalars().all()
        for rule in rules:
            await self._add_outbox(
                rule,
                task,
                check,
                event_type,
                f"{rule.id}:{check.id}:{event_type}",
                {"domain_id": str(task.managed_domain_id), "check_id": str(check.id), "event_type": event_type},
                at,
            )

    async def _queue_expiration_notifications(
        self, task: DomainRefreshTask, check: DomainCheck, at: datetime
    ) -> None:
        expires_at = self._snapshot_datetime((check.snapshot or {}).get("expires_at"))
        if expires_at is None:
            return
        rules = (await self._session.execute(
            select(NotificationRule).where(NotificationRule.user_id == task.user_id, NotificationRule.is_enabled.is_(True), NotificationRule.event_type == "expiration", or_(NotificationRule.managed_domain_id.is_(None), NotificationRule.managed_domain_id == task.managed_domain_id))
        )).scalars().all()
        for rule in rules:
            if rule.days_before is not None and expires_at <= at + timedelta(days=rule.days_before):
                await self._add_outbox(rule, task, check, "expiration", f"{rule.id}:{expires_at.date()}:{rule.days_before}", {"domain_id": str(task.managed_domain_id), "check_id": str(check.id), "event_type": "expiration", "expires_at": expires_at.isoformat()}, at)

    async def _add_outbox(self, rule: NotificationRule, task: DomainRefreshTask, check: DomainCheck, event_type: str, deduplication_key: str, payload: dict, at: datetime) -> None:
        try:
            async with self._session.begin_nested():
                self._session.add(NotificationOutbox(id=uuid4(), notification_rule_id=rule.id, managed_domain_id=task.managed_domain_id, domain_check_id=check.id, deduplication_key=deduplication_key, event_type=event_type, payload=payload, status="pending", attempt_count=0, available_at=at, created_at=at, updated_at=at))
                await self._session.flush()
        except IntegrityError:
            return

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

    async def _previous_success_snapshot(
        self, domain_id: UUID
    ) -> dict | None:
        result = await self._session.execute(
            select(DomainCheck.snapshot)
            .where(
                DomainCheck.managed_domain_id == domain_id,
                DomainCheck.outcome == "success",
                DomainCheck.snapshot.is_not(None),
            )
            .order_by(DomainCheck.checked_at.desc(), DomainCheck.id.desc())
            .limit(1)
        )
        snapshot = result.scalar_one_or_none()
        return dict(snapshot) if snapshot is not None else None

    @staticmethod
    def _snapshot_hash(snapshot: dict) -> str:
        payload = json.dumps(
            snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _changed_fields(previous: dict | None, current: dict) -> list[str]:
        if previous is None:
            return []
        monitored_fields = (
            "registrar",
            "statuses",
            "registered_at",
            "expires_at",
            "updated_at",
            "nameservers",
            "dnssec_enabled",
        )
        return [field for field in monitored_fields if previous.get(field) != current.get(field)]

    @staticmethod
    def _snapshot_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        raise TypeError("snapshot datetime fields must be RFC 3339 timestamps")

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
            result_code=task.result_code,
            result_message=task.result_message,
            source_check_id=task.source_check_id,
            fresh_until=as_utc(task.fresh_until),
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
