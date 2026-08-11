from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from domainsmanager_lookup.memory_store import MemoryLookupStore
from domainsmanager_lookup.store import StoredLookupRecord

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def record(*, offset: int = 0) -> StoredLookupRecord:
    return StoredLookupRecord(
        record_id=uuid4(),
        namespace="response:rdap:v1",
        cache_key="example.com",
        schema_version=1,
        payload=b"payload",
        payload_codec="raw",
        content_hash=str(offset),
        observed_at=NOW + timedelta(seconds=offset),
        fresh_until=NOW + timedelta(hours=1),
    )


@pytest.mark.asyncio
@pytest.mark.contract
async def test_memory_store_does_not_allow_old_publish_to_replace_head() -> None:
    store = MemoryLookupStore()
    newer = record(offset=2)
    older = record(offset=1)

    await store.publish(newer)
    await store.publish(older)

    assert await store.get_current(newer.namespace, newer.cache_key) == newer


@pytest.mark.asyncio
@pytest.mark.contract
async def test_memory_store_marks_current_record_unusable() -> None:
    store = MemoryLookupStore()
    current = record()
    await store.publish(current)

    await store.mark_unusable(current.record_id, "invalid payload")

    assert await store.get_current(current.namespace, current.cache_key) is None


@pytest.mark.asyncio
@pytest.mark.concurrency
async def test_memory_store_lease_is_token_safe_and_exclusive() -> None:
    store = MemoryLookupStore()
    first = await store.try_acquire_lease(
        "response:rdap:v1", "example.com", "worker-1", timedelta(seconds=30)
    )
    second = await store.try_acquire_lease(
        "response:rdap:v1", "example.com", "worker-2", timedelta(seconds=30)
    )

    assert first is not None
    assert second is None
    await store.release_lease(first)
    assert await store.try_acquire_lease(
        "response:rdap:v1", "example.com", "worker-2", timedelta(seconds=30)
    ) is not None
