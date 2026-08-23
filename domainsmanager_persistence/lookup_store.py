from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domainsmanager_lookup.store import LookupStore, RefreshLease, StoredLookupRecord
from domainsmanager_persistence.models import (
    LookupCacheHead,
    LookupRecord,
    LookupRefreshLease,
)


class SqlAlchemyLookupStore(LookupStore):
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_current(
        self,
        namespace: str,
        cache_key: str,
    ) -> StoredLookupRecord | None:
        async with self._sessions() as session:
            result = await session.execute(
                select(LookupRecord)
                .join(LookupCacheHead, LookupCacheHead.record_id == LookupRecord.id)
                .where(
                    LookupCacheHead.namespace == namespace,
                    LookupCacheHead.cache_key == cache_key,
                    LookupRecord.is_usable.is_(True),
                )
            )
            row = result.scalar_one_or_none()
            return self._to_record(row) if row is not None else None

    async def publish(self, record: StoredLookupRecord) -> None:
        now = datetime.now(timezone.utc)
        async with self._sessions() as session, session.begin():
            await session.execute(self._record_insert(session, record, now))
            effective_id = await self._effective_record_id(session, record)
            await session.execute(
                self._head_upsert(
                    session,
                    record,
                    effective_id,
                    now,
                )
            )

    async def mark_unusable(self, record_id: UUID, reason: str) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(LookupRecord)
                .where(LookupRecord.id == record_id)
                .values(is_usable=False, unusable_reason=reason[:256])
            )
            await session.execute(
                delete(LookupCacheHead).where(LookupCacheHead.record_id == record_id)
            )

    async def try_acquire_lease(
        self,
        namespace: str,
        cache_key: str,
        owner: str,
        ttl: timedelta,
    ) -> RefreshLease | None:
        now = datetime.now(timezone.utc)
        token = uuid4()
        expires_at = now + ttl
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                self._lease_upsert(
                    session,
                    namespace,
                    cache_key,
                    token,
                    owner,
                    expires_at,
                    now,
                )
            )
            acquired = result.first()
            if acquired is None:
                return None
        return RefreshLease(namespace, cache_key, token, owner, expires_at)

    async def release_lease(self, lease: RefreshLease) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                delete(LookupRefreshLease).where(
                    LookupRefreshLease.namespace == lease.namespace,
                    LookupRefreshLease.cache_key == lease.cache_key,
                    LookupRefreshLease.lease_token == lease.token,
                )
            )

    @staticmethod
    def _insert_for(session: AsyncSession, table):
        if session.bind is None:
            raise RuntimeError("database session is not bound")
        if session.bind.dialect.name == "postgresql":
            return postgresql_insert(table)
        if session.bind.dialect.name == "sqlite":
            return sqlite_insert(table)
        raise NotImplementedError(
            f"lookup store does not support {session.bind.dialect.name}"
        )

    @classmethod
    def _record_insert(
        cls,
        session: AsyncSession,
        record: StoredLookupRecord,
        now: datetime,
    ):
        return (
            cls._insert_for(session, LookupRecord)
            .values(
                id=record.record_id,
                namespace=record.namespace,
                cache_key=record.cache_key,
                schema_version=record.schema_version,
                record_kind=record.record_kind,
                protocol=record.protocol,
                endpoint=record.endpoint,
                status_code=record.status_code,
                payload=record.payload,
                payload_codec=record.payload_codec,
                plaintext_size=len(record.payload),
                content_hash=record.content_hash,
                observed_at=record.observed_at,
                fresh_until=record.fresh_until,
                stale_until=record.stale_until,
                retry_after=record.retry_after,
                created_at=now,
            )
            .on_conflict_do_nothing()
        )

    @staticmethod
    async def _effective_record_id(
        session: AsyncSession,
        record: StoredLookupRecord,
    ) -> UUID:
        existing = await session.get(LookupRecord, record.record_id)
        if existing is not None:
            if (
                existing.namespace != record.namespace
                or existing.cache_key != record.cache_key
                or existing.content_hash != record.content_hash
                or SqlAlchemyLookupStore._as_aware(existing.observed_at)
                != record.observed_at
            ):
                raise ValueError("lookup record ID already contains different data")
            return existing.id
        result = await session.execute(
            select(LookupRecord.id).where(
                LookupRecord.namespace == record.namespace,
                LookupRecord.cache_key == record.cache_key,
                LookupRecord.content_hash == record.content_hash,
                LookupRecord.observed_at == record.observed_at,
            )
        )
        effective_id = result.scalar_one_or_none()
        if effective_id is None:
            raise RuntimeError("lookup record insert did not create or reuse a row")
        return effective_id

    @classmethod
    def _head_upsert(
        cls,
        session: AsyncSession,
        record: StoredLookupRecord,
        effective_id: UUID,
        now: datetime,
    ):
        statement = cls._insert_for(session, LookupCacheHead).values(
            namespace=record.namespace,
            cache_key=record.cache_key,
            record_id=effective_id,
            observed_at=record.observed_at,
            updated_at=now,
        )
        excluded = statement.excluded
        return statement.on_conflict_do_update(
            index_elements=[
                LookupCacheHead.namespace,
                LookupCacheHead.cache_key,
            ],
            set_={
                "record_id": excluded.record_id,
                "observed_at": excluded.observed_at,
                "updated_at": excluded.updated_at,
            },
            where=or_(
                excluded.observed_at > LookupCacheHead.observed_at,
                and_(
                    excluded.observed_at == LookupCacheHead.observed_at,
                    excluded.record_id > LookupCacheHead.record_id,
                ),
            ),
        )

    @classmethod
    def _lease_upsert(
        cls,
        session: AsyncSession,
        namespace: str,
        cache_key: str,
        token: UUID,
        owner: str,
        expires_at: datetime,
        now: datetime,
    ):
        statement = cls._insert_for(session, LookupRefreshLease).values(
            namespace=namespace,
            cache_key=cache_key,
            lease_token=token,
            lease_owner=owner,
            lease_until=expires_at,
            updated_at=now,
        )
        excluded = statement.excluded
        return statement.on_conflict_do_update(
            index_elements=[
                LookupRefreshLease.namespace,
                LookupRefreshLease.cache_key,
            ],
            set_={
                "lease_token": excluded.lease_token,
                "lease_owner": excluded.lease_owner,
                "lease_until": excluded.lease_until,
                "updated_at": excluded.updated_at,
            },
            where=LookupRefreshLease.lease_until <= now,
        ).returning(LookupRefreshLease.lease_token)

    @staticmethod
    def _to_record(row: LookupRecord) -> StoredLookupRecord:
        return StoredLookupRecord(
            record_id=row.id,
            namespace=row.namespace,
            cache_key=row.cache_key,
            schema_version=row.schema_version,
            payload=row.payload,
            payload_codec=row.payload_codec,
            content_hash=row.content_hash,
            observed_at=SqlAlchemyLookupStore._as_aware(row.observed_at),
            fresh_until=SqlAlchemyLookupStore._as_aware(row.fresh_until),
            stale_until=SqlAlchemyLookupStore._as_aware(row.stale_until),
            retry_after=SqlAlchemyLookupStore._as_aware(row.retry_after),
            record_kind=row.record_kind,
            protocol=row.protocol,
            endpoint=row.endpoint,
            status_code=row.status_code,
        )

    @staticmethod
    def _as_aware(value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)
