from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID, uuid4

from domainsmanager_application.auth import (
    DuplicateRecordError,
    UnitOfWork,
    UnitOfWorkFactory,
)
from domainsmanager_lookup import DomainLookup, InvalidDomainError


class DomainError(RuntimeError):
    code = "domain_error"


class DomainNotFoundError(DomainError):
    code = "not_found"


class DomainAlreadyManagedError(DomainError):
    code = "domain_already_managed"


class InvalidManagedDomainError(DomainError):
    code = "invalid_domain"


class SubdomainNotSupportedError(DomainError):
    code = "subdomain_not_supported"


class DomainVersionConflictError(DomainError):
    code = "version_conflict"


@dataclass(frozen=True, slots=True)
class ManagedDomainRecord:
    id: UUID
    user_id: UUID
    name_ascii: str
    name_unicode: str
    registrable_domain: str
    public_suffix: str
    tld: str
    monitor_enabled: bool
    renewal_mode: str | None
    notes: str | None
    registered_at: datetime | None = field(default=None, kw_only=True)
    expires_at: datetime | None
    registry_expires_at: datetime | None = field(default=None, kw_only=True)
    registrar_expires_at: datetime | None = field(default=None, kw_only=True)
    expiration_status: str = field(default="unknown", kw_only=True)
    expiration_checked_at: datetime | None = field(default=None, kw_only=True)
    registrar_rdap_url: str | None = field(default=None, kw_only=True)
    registry_updated_at: datetime | None = field(default=None, kw_only=True)
    dnssec_enabled: bool | None = field(default=None, kw_only=True)
    last_check_at: datetime | None
    last_outcome: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


DomainSort = Literal[
    "created_at",
    "-created_at",
    "name",
    "-name",
    "expires_at",
    "-expires_at",
    "last_check_at",
    "-last_check_at",
]


@dataclass(frozen=True, slots=True)
class DomainListQuery:
    page: int = 1
    page_size: int = 20
    query: str | None = None
    monitor_enabled: bool | None = None
    expires_from: datetime | None = None
    expires_to: datetime | None = None
    last_outcome: str | None = None
    sort: DomainSort = "-created_at"
    lifecycle: Literal["expiring", "expired"] | None = None


@dataclass(frozen=True, slots=True)
class DomainPage:
    items: list[ManagedDomainRecord]
    total: int
    page: int
    page_size: int


DEFAULT_EXPIRATION_WARNING_DAYS = 30


def warning_days_from_preferences(preferences: dict[str, object] | None) -> int:
    raw = None if preferences is None else preferences.get("expiration_warning_days")
    if not isinstance(raw, list):
        return DEFAULT_EXPIRATION_WARNING_DAYS
    days = [item for item in raw if isinstance(item, int)]
    return max(days) if days else DEFAULT_EXPIRATION_WARNING_DAYS


@dataclass(frozen=True, slots=True)
class DomainStats:
    managed: int
    monitored: int
    expiring: int
    expired: int
    warning_days: int


@dataclass(frozen=True, slots=True)
class ScheduledDomain:
    id: UUID
    user_id: UUID
    name_ascii: str


class ManagedDomainRepository(Protocol):
    async def get(
        self, user_id: UUID, domain_id: UUID, *, include_deleted: bool = False
    ) -> ManagedDomainRecord | None: ...

    async def get_any(self, domain_id: UUID) -> ManagedDomainRecord | None: ...

    async def get_by_name(
        self, user_id: UUID, name_ascii: str, *, for_update: bool = False
    ) -> ManagedDomainRecord | None: ...

    async def list(self, user_id: UUID, query: DomainListQuery) -> DomainPage: ...

    async def summarize(
        self, user_id: UUID, *, now: datetime, warning_days: int
    ) -> DomainStats: ...

    async def add(self, record: ManagedDomainRecord) -> None: ...

    async def restore(
        self, domain_id: UUID, user_id: UUID, at: datetime, *, monitor_enabled: bool
    ) -> ManagedDomainRecord | None: ...

    async def update_local(
        self,
        domain_id: UUID,
        user_id: UUID,
        version: int,
        *,
        monitor_enabled: bool | None,
        renewal_mode: str | None,
        notes: str | None,
        fields: frozenset[str],
        updated_at: datetime,
    ) -> ManagedDomainRecord | None: ...

    async def soft_delete(
        self, domain_id: UUID, user_id: UUID, at: datetime
    ) -> bool: ...

    async def claim_due(
        self, now: datetime, next_check_at: datetime, limit: int
    ) -> list[ScheduledDomain]: ...

    async def list_expiration_backfill_candidates(
        self, limit: int
    ) -> list[ScheduledDomain]: ...


class DomainService:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        lookup: DomainLookup,
        clock: Callable[[], datetime] | None = None,
        initial_task_max_attempts: int = 5,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._lookup = lookup
        self._clock = clock or (lambda: datetime.now(UTC))
        self._initial_task_max_attempts = initial_task_max_attempts

    async def create(
        self, user_id: UUID, name: str, *, monitor_enabled: bool = True
    ) -> tuple[ManagedDomainRecord, bool]:
        try:
            identity = self._lookup.normalize(name)
        except InvalidDomainError as error:
            raise InvalidManagedDomainError(str(error)) from error
        if identity.ascii_name != identity.registrable_domain:
            raise SubdomainNotSupportedError("only registrable domains are supported")
        now = self._clock()
        record = ManagedDomainRecord(
            id=uuid4(),
            user_id=user_id,
            name_ascii=identity.ascii_name,
            name_unicode=identity.unicode_name,
            registrable_domain=identity.registrable_domain,
            public_suffix=identity.public_suffix,
            tld=identity.tld,
            monitor_enabled=monitor_enabled,
            renewal_mode=None,
            notes=None,
            registered_at=None,
            expires_at=None,
            registry_updated_at=None,
            dnssec_enabled=None,
            last_check_at=None,
            last_outcome=None,
            version=1,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        async with self._unit_of_work() as uow:
            existing = await uow.domains.get_by_name(
                user_id, identity.ascii_name, for_update=True
            )
            if existing is not None:
                if existing.deleted_at is None:
                    raise DomainAlreadyManagedError("domain is already managed")
                restored = await uow.domains.restore(
                    existing.id, user_id, now, monitor_enabled=monitor_enabled
                )
                if restored is None:
                    raise DomainAlreadyManagedError("domain is already managed")
                if restored.monitor_enabled:
                    await self._enqueue_initial_refresh(uow, restored, now)
                await uow.commit()
                return restored, True
            try:
                await uow.domains.add(record)
            except DuplicateRecordError as error:
                raise DomainAlreadyManagedError("domain is already managed") from error
            if record.monitor_enabled:
                await self._enqueue_initial_refresh(uow, record, now)
            await uow.commit()
        return record, False

    async def get(self, user_id: UUID, domain_id: UUID) -> ManagedDomainRecord:
        async with self._unit_of_work() as uow:
            record = await uow.domains.get(user_id, domain_id)
        if record is None:
            raise DomainNotFoundError("domain was not found")
        return record

    async def list(self, user_id: UUID, query: DomainListQuery) -> DomainPage:
        async with self._unit_of_work() as uow:
            return await uow.domains.list(
                user_id, await self._resolved_list_query(uow, user_id, query)
            )

    async def stats(self, user_id: UUID) -> DomainStats:
        now = self._clock()
        async with self._unit_of_work() as uow:
            warning_days = await self._warning_days(uow, user_id)
            return await uow.domains.summarize(
                user_id, now=now, warning_days=warning_days
            )

    async def _warning_days(self, uow: UnitOfWork, user_id: UUID) -> int:
        user = await uow.users.get_by_id(user_id)
        return warning_days_from_preferences(None if user is None else user.preferences)

    async def _resolved_list_query(
        self, uow: UnitOfWork, user_id: UUID, query: DomainListQuery
    ) -> DomainListQuery:
        if query.lifecycle is None:
            return query
        if query.expires_from is not None or query.expires_to is not None:
            raise InvalidManagedDomainError(
                "lifecycle cannot be combined with expires_from or expires_to"
            )
        now = self._clock()
        warning_days = await self._warning_days(uow, user_id)
        if query.lifecycle == "expired":
            return DomainListQuery(
                page=query.page,
                page_size=query.page_size,
                query=query.query,
                monitor_enabled=query.monitor_enabled,
                expires_from=None,
                expires_to=now,
                last_outcome=query.last_outcome,
                sort=query.sort,
                lifecycle="expired",
            )
        return DomainListQuery(
            page=query.page,
            page_size=query.page_size,
            query=query.query,
            monitor_enabled=query.monitor_enabled,
            expires_from=now,
            expires_to=now + timedelta(days=warning_days),
            last_outcome=query.last_outcome,
            sort=query.sort,
            lifecycle="expiring",
        )

    async def update(
        self,
        user_id: UUID,
        domain_id: UUID,
        version: int,
        *,
        monitor_enabled: bool | None,
        renewal_mode: str | None,
        notes: str | None,
        fields: frozenset[str],
    ) -> ManagedDomainRecord:
        now = self._clock()
        async with self._unit_of_work() as uow:
            current = await uow.domains.get(user_id, domain_id)
            if current is None:
                raise DomainNotFoundError("domain was not found")
            record = await uow.domains.update_local(
                domain_id,
                user_id,
                version,
                monitor_enabled=monitor_enabled,
                renewal_mode=renewal_mode,
                notes=notes,
                fields=fields,
                updated_at=now,
            )
            if record is not None:
                if (
                    "monitor_enabled" in fields
                    and monitor_enabled is True
                    and not current.monitor_enabled
                ):
                    await self._enqueue_initial_refresh(uow, record, now)
                await uow.commit()
                return record
        raise DomainVersionConflictError("domain has changed")

    async def _enqueue_initial_refresh(
        self, uow: UnitOfWork, record: ManagedDomainRecord, now: datetime
    ) -> None:
        from domainsmanager_application.tasks import RefreshTaskRecord

        key = f"monitor-enabled:{record.id}:{now.isoformat()}"
        await uow.tasks.add(
            RefreshTaskRecord(
                id=uuid4(),
                user_id=record.user_id,
                domain_id=record.id,
                domain_name=record.name_ascii,
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
                max_attempts=self._initial_task_max_attempts,
            ),
            key,
            sha256(b"monitor-enabled-initial-refresh").hexdigest(),
            now + timedelta(days=1),
        )

    async def delete(self, user_id: UUID, domain_id: UUID) -> None:
        now = self._clock()
        async with self._unit_of_work() as uow:
            current = await uow.domains.get(user_id, domain_id, include_deleted=True)
            if current is None:
                raise DomainNotFoundError("domain was not found")
            if current.deleted_at is not None:
                return
            if await uow.domains.soft_delete(domain_id, user_id, now):
                await uow.commit()
