from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import asc, desc, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from domainsmanager_application.auth import DuplicateRecordError
from domainsmanager_application.domains import (
    DomainListQuery,
    DomainPage,
    ManagedDomainRecord,
    ScheduledDomain,
)
from domainsmanager_persistence.models import ManagedDomain


def as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class SqlAlchemyDomainRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, user_id: UUID, domain_id: UUID, *, include_deleted: bool = False
    ) -> ManagedDomainRecord | None:
        statement = select(ManagedDomain).where(
            ManagedDomain.id == domain_id, ManagedDomain.user_id == user_id
        )
        if not include_deleted:
            statement = statement.where(ManagedDomain.deleted_at.is_(None))
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return self._to_record(row) if row is not None else None

    async def get_any(self, domain_id: UUID) -> ManagedDomainRecord | None:
        result = await self._session.execute(
            select(ManagedDomain).where(ManagedDomain.id == domain_id)
        )
        row = result.scalar_one_or_none()
        return self._to_record(row) if row is not None else None

    async def get_by_name(
        self, user_id: UUID, name_ascii: str, *, for_update: bool = False
    ) -> ManagedDomainRecord | None:
        statement = select(ManagedDomain).where(
            ManagedDomain.user_id == user_id, ManagedDomain.name_ascii == name_ascii
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return self._to_record(row) if row is not None else None

    async def list(self, user_id: UUID, query: DomainListQuery) -> DomainPage:
        filters = [ManagedDomain.user_id == user_id, ManagedDomain.deleted_at.is_(None)]
        if query.query:
            pattern = f"%{query.query}%"
            filters.append(
                or_(
                    ManagedDomain.name_ascii.ilike(pattern),
                    ManagedDomain.name_unicode.ilike(pattern),
                )
            )
        if query.monitor_enabled is not None:
            filters.append(ManagedDomain.monitor_enabled == query.monitor_enabled)
        if query.expires_from is not None:
            filters.append(ManagedDomain.registrar_expires_at >= query.expires_from)
        if query.expires_to is not None:
            filters.append(ManagedDomain.registrar_expires_at <= query.expires_to)
        if query.last_outcome is not None:
            filters.append(ManagedDomain.last_outcome == query.last_outcome)

        count = await self._session.execute(
            select(func.count()).select_from(ManagedDomain).where(*filters)
        )
        statement = select(ManagedDomain).where(*filters)
        column_name = query.sort.removeprefix("-")
        column = {
            "name": ManagedDomain.name_ascii,
            "created_at": ManagedDomain.created_at,
            "expires_at": ManagedDomain.registrar_expires_at,
            "last_check_at": ManagedDomain.last_check_at,
        }[column_name]
        direction = desc if query.sort.startswith("-") else asc
        if column_name in {"expires_at", "last_check_at"}:
            statement = statement.order_by(
                direction(column).nulls_last(), asc(ManagedDomain.id)
            )
        else:
            statement = statement.order_by(direction(column), asc(ManagedDomain.id))
        statement = statement.offset((query.page - 1) * query.page_size).limit(
            query.page_size
        )
        rows = (await self._session.execute(statement)).scalars().all()
        return DomainPage(
            items=[self._to_record(row) for row in rows],
            total=count.scalar_one(),
            page=query.page,
            page_size=query.page_size,
        )

    async def add(self, record: ManagedDomainRecord) -> None:
        self._session.add(
            ManagedDomain(
                id=record.id,
                user_id=record.user_id,
                name_ascii=record.name_ascii,
                name_unicode=record.name_unicode,
                registrable_domain=record.registrable_domain,
                public_suffix=record.public_suffix,
                tld=record.tld,
                statuses=[],
                nameservers=[],
                monitor_enabled=record.monitor_enabled,
                renewal_mode=record.renewal_mode,
                notes=record.notes,
                version=record.version,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )
        try:
            await self._session.flush()
        except IntegrityError as error:
            raise DuplicateRecordError("domain already exists") from error

    async def restore(
        self, domain_id: UUID, user_id: UUID, at: datetime, *, monitor_enabled: bool
    ) -> ManagedDomainRecord | None:
        result = await self._session.execute(
            update(ManagedDomain)
            .where(
                ManagedDomain.id == domain_id,
                ManagedDomain.user_id == user_id,
                ManagedDomain.deleted_at.is_not(None),
            )
            .values(
                deleted_at=None,
                deleted_by_user_id=None,
                monitor_enabled=monitor_enabled,
                updated_at=at,
                version=ManagedDomain.version + 1,
            )
            .returning(ManagedDomain)
        )
        row = result.scalar_one_or_none()
        return self._to_record(row) if row is not None else None

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
    ) -> ManagedDomainRecord | None:
        values: dict[str, object] = {
            "updated_at": updated_at,
            "version": ManagedDomain.version + 1,
        }
        for field, value in (
            ("monitor_enabled", monitor_enabled),
            ("renewal_mode", renewal_mode),
            ("notes", notes),
        ):
            if field in fields:
                values[field] = value
        result = await self._session.execute(
            update(ManagedDomain)
            .where(
                ManagedDomain.id == domain_id,
                ManagedDomain.user_id == user_id,
                ManagedDomain.deleted_at.is_(None),
                ManagedDomain.version == version,
            )
            .values(**values)
            .returning(ManagedDomain)
        )
        row = result.scalar_one_or_none()
        return self._to_record(row) if row is not None else None

    async def soft_delete(self, domain_id: UUID, user_id: UUID, at: datetime) -> bool:
        result = await self._session.execute(
            update(ManagedDomain)
            .where(
                ManagedDomain.id == domain_id,
                ManagedDomain.user_id == user_id,
                ManagedDomain.deleted_at.is_(None),
            )
            .values(
                deleted_at=at,
                deleted_by_user_id=user_id,
                updated_at=at,
                version=ManagedDomain.version + 1,
            )
        )
        return result.rowcount == 1

    async def claim_due(
        self, now: datetime, next_check_at: datetime, limit: int
    ) -> list[ScheduledDomain]:
        rows = (
            (
                await self._session.execute(
                    select(ManagedDomain)
                    .where(
                        ManagedDomain.monitor_enabled.is_(True),
                        ManagedDomain.deleted_at.is_(None),
                        ManagedDomain.next_check_at.is_not(None),
                        ManagedDomain.next_check_at <= now,
                    )
                    .order_by(ManagedDomain.next_check_at, ManagedDomain.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        claimed: list[ScheduledDomain] = []
        for domain in rows:
            domain.next_check_at = next_check_at
            domain.updated_at = now
            domain.version += 1
            claimed.append(
                ScheduledDomain(
                    id=domain.id,
                    user_id=domain.user_id,
                    name_ascii=domain.name_ascii,
                )
            )
        await self._session.flush()
        return claimed

    async def list_expiration_backfill_candidates(
        self, limit: int
    ) -> list[ScheduledDomain]:
        rows = (
            await self._session.execute(
                select(ManagedDomain)
                .where(
                    ManagedDomain.deleted_at.is_(None),
                    ManagedDomain.expiration_status == "unknown",
                )
                .order_by(ManagedDomain.created_at, ManagedDomain.id)
                .limit(limit)
            )
        ).scalars()
        return [
            ScheduledDomain(
                id=domain.id,
                user_id=domain.user_id,
                name_ascii=domain.name_ascii,
            )
            for domain in rows
        ]

    @staticmethod
    def _to_record(row: ManagedDomain) -> ManagedDomainRecord:
        return ManagedDomainRecord(
            id=row.id,
            user_id=row.user_id,
            name_ascii=row.name_ascii,
            name_unicode=row.name_unicode,
            registrable_domain=row.registrable_domain,
            public_suffix=row.public_suffix,
            tld=row.tld,
            monitor_enabled=row.monitor_enabled,
            renewal_mode=row.renewal_mode,
            notes=row.notes,
            registered_at=as_utc(row.registered_at),
            expires_at=as_utc(row.expires_at),
            registry_expires_at=as_utc(row.registry_expires_at),
            registrar_expires_at=as_utc(row.registrar_expires_at),
            expiration_status=row.expiration_status,
            expiration_checked_at=as_utc(row.expiration_checked_at),
            registrar_rdap_url=row.registrar_rdap_url,
            registry_updated_at=as_utc(row.registry_updated_at),
            dnssec_enabled=row.dnssec_enabled,
            last_check_at=as_utc(row.last_check_at),
            last_outcome=row.last_outcome,
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            deleted_at=as_utc(row.deleted_at),
        )
