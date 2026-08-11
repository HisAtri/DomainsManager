from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
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
            row = await session.get(LookupRecord, record.record_id)
            if row is None:
                row = LookupRecord(
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
                session.add(row)
                await session.flush()

            head = await session.get(
                LookupCacheHead,
                (record.namespace, record.cache_key),
            )
            if head is None:
                session.add(
                    LookupCacheHead(
                        namespace=record.namespace,
                        cache_key=record.cache_key,
                        record_id=record.record_id,
                        observed_at=record.observed_at,
                        updated_at=now,
                    )
                )
            elif (record.observed_at, str(record.record_id)) > (
                self._as_aware(head.observed_at),
                str(head.record_id),
            ):
                head.record_id = record.record_id
                head.observed_at = record.observed_at
                head.updated_at = now

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
            lease = await session.get(
                LookupRefreshLease,
                (namespace, cache_key),
                with_for_update=True,
            )
            if lease is None:
                lease = LookupRefreshLease(
                    namespace=namespace,
                    cache_key=cache_key,
                    lease_token=token,
                    lease_owner=owner,
                    lease_until=expires_at,
                    updated_at=now,
                )
                session.add(lease)
            elif self._as_aware(lease.lease_until) <= now:
                lease.lease_token = token
                lease.lease_owner = owner
                lease.lease_until = expires_at
                lease.updated_at = now
            else:
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
