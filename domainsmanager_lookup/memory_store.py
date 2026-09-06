from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from domainsmanager_lookup.store import (
    EndpointGateLease,
    EndpointGateState,
    LookupStore,
    RefreshLease,
    StoredLookupRecord,
)


class MemoryLookupStore(LookupStore):
    def __init__(self) -> None:
        self._records: dict[UUID, StoredLookupRecord] = {}
        self._heads: dict[tuple[str, str], UUID] = {}
        self._unusable: dict[UUID, str] = {}
        self._leases: dict[tuple[str, str], RefreshLease] = {}
        self._endpoint_gates: dict[
            tuple[str, str], tuple[EndpointGateState, EndpointGateLease | None]
        ] = {}
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
        now = datetime.now(UTC)
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

    async def try_acquire_endpoint_gate(
        self,
        protocol: str,
        endpoint: str,
        owner: str,
        ttl: timedelta,
    ) -> EndpointGateLease | None:
        del owner
        now = datetime.now(UTC)
        key = (protocol, endpoint)
        async with self._lock:
            state, current = self._endpoint_gates.get(
                key, (EndpointGateState(None, None, 0), None)
            )
            if state.blocked_until is not None and state.blocked_until > now:
                return None
            if current is not None and current.expires_at > now:
                return None
            lease = EndpointGateLease(protocol, endpoint, uuid4(), now + ttl)
            self._endpoint_gates[key] = (
                EndpointGateState(
                    state.blocked_until, lease.expires_at, state.failure_count
                ),
                lease,
            )
            return lease

    async def get_endpoint_gate_state(
        self, protocol: str, endpoint: str
    ) -> EndpointGateState | None:
        async with self._lock:
            item = self._endpoint_gates.get((protocol, endpoint))
            return item[0] if item is not None else None

    async def release_endpoint_gate(
        self, lease: EndpointGateLease, *, succeeded: bool
    ) -> None:
        key = (lease.protocol, lease.endpoint)
        async with self._lock:
            item = self._endpoint_gates.get(key)
            if item is None or item[1] is None or item[1].token != lease.token:
                return
            state = item[0]
            self._endpoint_gates[key] = (
                EndpointGateState(
                    None if succeeded else state.blocked_until,
                    None,
                    0 if succeeded else state.failure_count,
                ),
                None,
            )

    async def block_endpoint_gate(
        self,
        lease: EndpointGateLease,
        *,
        retry_after: datetime | None,
        retry_base: timedelta,
        retry_max: timedelta,
    ) -> datetime:
        key = (lease.protocol, lease.endpoint)
        now = datetime.now(UTC)
        async with self._lock:
            item = self._endpoint_gates.get(key)
            if item is None or item[1] is None or item[1].token != lease.token:
                if item is not None and item[0].blocked_until is not None:
                    return item[0].blocked_until
                return now
            state = item[0]
            failures = state.failure_count + 1
            delay = min(retry_base * (2 ** (failures - 1)), retry_max)
            blocked_until = retry_after if retry_after is not None else now + delay
            blocked_until = max(now, blocked_until)
            self._endpoint_gates[key] = (
                EndpointGateState(blocked_until, None, failures),
                None,
            )
            return blocked_until

    @staticmethod
    def _is_newer(candidate: StoredLookupRecord, current: StoredLookupRecord) -> bool:
        return (candidate.observed_at, str(candidate.record_id)) > (
            current.observed_at,
            str(current.record_id),
        )
