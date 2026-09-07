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


@dataclass(frozen=True, slots=True)
class EndpointGateLease:
    protocol: str
    endpoint: str
    token: UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class EndpointGateState:
    blocked_until: datetime | None
    lease_until: datetime | None
    failure_count: int


def endpoint_retry_delay(
    failures: int, base: timedelta, maximum: timedelta
) -> timedelta:
    """Saturate before multiplying, including after a prolonged upstream outage."""
    delay = min(base, maximum)
    if delay <= timedelta(0):
        return timedelta(0)
    for _ in range(max(0, failures - 1)):
        if delay >= maximum - delay:
            return maximum
        delay += delay
    return delay


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

    async def try_acquire_endpoint_gate(
        self,
        protocol: str,
        endpoint: str,
        owner: str,
        ttl: timedelta,
    ) -> EndpointGateLease | None: ...

    async def get_endpoint_gate_state(
        self, protocol: str, endpoint: str
    ) -> EndpointGateState | None: ...

    async def release_endpoint_gate(
        self, lease: EndpointGateLease, *, succeeded: bool
    ) -> None: ...

    async def renew_endpoint_gate(
        self, lease: EndpointGateLease, ttl: timedelta
    ) -> bool: ...

    async def block_endpoint_gate(
        self,
        lease: EndpointGateLease,
        *,
        retry_after: datetime | None,
        retry_base: timedelta,
        retry_max: timedelta,
    ) -> datetime: ...
