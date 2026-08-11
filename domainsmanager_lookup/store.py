from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True, slots=True)
class StoredLookupRecord:
    record_id: UUID
    namespace: str
    cache_key: str
    schema_version: int
    payload: bytes
    payload_codec: str
    content_hash: str
    observed_at: datetime
    fresh_until: datetime
    stale_until: datetime | None = None
    retry_after: datetime | None = None
    record_kind: str = "success"
    protocol: str | None = None
    endpoint: str | None = None
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class RefreshLease:
    namespace: str
    cache_key: str
    token: UUID
    owner: str
    expires_at: datetime


@runtime_checkable
class LookupStore(Protocol):
    async def get_current(
        self,
        namespace: str,
        cache_key: str,
    ) -> StoredLookupRecord | None: ...

    async def publish(self, record: StoredLookupRecord) -> None: ...

    async def mark_unusable(self, record_id: UUID, reason: str) -> None: ...

    async def try_acquire_lease(
        self,
        namespace: str,
        cache_key: str,
        owner: str,
        ttl: timedelta,
    ) -> RefreshLease | None: ...

    async def release_lease(self, lease: RefreshLease) -> None: ...
