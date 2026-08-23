from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

from domainsmanager_application.auth import DuplicateRecordError, UnitOfWorkFactory
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
    expires_at: datetime | None
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
    sort: DomainSort = "name"


@dataclass(frozen=True, slots=True)
class DomainPage:
    items: list[ManagedDomainRecord]
    total: int
    page: int
    page_size: int


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

    async def add(self, record: ManagedDomainRecord) -> None: ...

    async def restore(
        self, domain_id: UUID, user_id: UUID, at: datetime
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


class DomainService:
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
            expires_at=None,
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
                restored = await uow.domains.restore(existing.id, user_id, now)
                if restored is None:
                    raise DomainAlreadyManagedError("domain is already managed")
                await uow.commit()
                return restored, True
            try:
                await uow.domains.add(record)
            except DuplicateRecordError as error:
                raise DomainAlreadyManagedError("domain is already managed") from error
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
            return await uow.domains.list(user_id, query)

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
                await uow.commit()
                return record
            current = await uow.domains.get(user_id, domain_id)
        if current is None:
            raise DomainNotFoundError("domain was not found")
        raise DomainVersionConflictError("domain has changed")

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
