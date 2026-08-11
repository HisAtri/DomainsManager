from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from domainsmanager_lookup.store import LookupStore, RefreshLease, StoredLookupRecord


class MemoryLookupStore(LookupStore):
    def __init__(self) -> None:
        self._records: dict[UUID, StoredLookupRecord] = {}
        self._heads: dict[tuple[str, str], UUID] = {}
        self._unusable: dict[UUID, str] = {}
        self._leases: dict[tuple[str, str], RefreshLease] = {}
        self._lock = asyncio.Lock()

    async def get_current(
        self,
        namespace: str,
        cache_key: str,
    ) -> StoredLookupRecord | None:
        async with self._lock:
            record_id = self._heads.get((namespace, cache_key))
            if record_id is None or record_id in self._unusable:
                return None
            return self._records.get(record_id)

    async def publish(self, record: StoredLookupRecord) -> None:
        key = (record.namespace, record.cache_key)
        async with self._lock:
            self._records.setdefault(record.record_id, record)
            current_id = self._heads.get(key)
            current = self._records.get(current_id) if current_id else None
            if current is None or self._is_newer(record, current):
                self._heads[key] = record.record_id

    async def mark_unusable(self, record_id: UUID, reason: str) -> None:
        async with self._lock:
            self._unusable[record_id] = reason
            for key, current_id in tuple(self._heads.items()):
                if current_id == record_id:
                    del self._heads[key]

    async def try_acquire_lease(
        self,
        namespace: str,
        cache_key: str,
        owner: str,
        ttl: timedelta,
    ) -> RefreshLease | None:
        now = datetime.now(timezone.utc)
        key = (namespace, cache_key)
        async with self._lock:
            current = self._leases.get(key)
            if current is not None and current.expires_at > now:
                return None
            lease = RefreshLease(
                namespace=namespace,
                cache_key=cache_key,
                token=uuid4(),
                owner=owner,
                expires_at=now + ttl,
            )
            self._leases[key] = lease
            return lease

    async def release_lease(self, lease: RefreshLease) -> None:
        key = (lease.namespace, lease.cache_key)
        async with self._lock:
            current = self._leases.get(key)
            if current is not None and current.token == lease.token:
                del self._leases[key]

    @staticmethod
    def _is_newer(candidate: StoredLookupRecord, current: StoredLookupRecord) -> bool:
        return (candidate.observed_at, str(candidate.record_id)) > (
            current.observed_at,
            str(current.record_id),
        )
