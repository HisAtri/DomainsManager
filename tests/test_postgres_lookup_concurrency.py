import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from domainsmanager_lookup.store import StoredLookupRecord
from domainsmanager_persistence.db import (
    create_engine,
    create_session_factory,
    run_migrations,
)
from domainsmanager_persistence.lookup_store import SqlAlchemyLookupStore
from tests.postgres import clean_project_schema, postgres_database

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def record(record_id: str, observed_offset: int, payload: bytes) -> StoredLookupRecord:
    return StoredLookupRecord(
        record_id=UUID(record_id),
        namespace="response:rdap:v1",
        cache_key="example.com",
        schema_version=1,
        payload=payload,
        payload_codec="raw",
        content_hash=payload.hex(),
        observed_at=NOW + timedelta(seconds=observed_offset),
        fresh_until=NOW + timedelta(hours=1),
    )


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.integration
@pytest.mark.concurrency
async def test_concurrent_publish_keeps_newest_head() -> None:
    config = postgres_database()
    await clean_project_schema(config)
    await run_migrations(config)
    engine = create_engine(config)
    store = SqlAlchemyLookupStore(create_session_factory(engine))
    older = record("00000000-0000-0000-0000-000000000001", 1, b"old")
    newer = record("00000000-0000-0000-0000-000000000002", 2, b"new")
    try:
        await asyncio.gather(store.publish(older), store.publish(newer))
        current = await store.get_current(older.namespace, older.cache_key)
        assert current is not None
        assert current.record_id == newer.record_id
    finally:
        await engine.dispose()
        await clean_project_schema(config)


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.integration
@pytest.mark.concurrency
async def test_concurrent_first_lease_has_one_owner() -> None:
    config = postgres_database()
    await clean_project_schema(config)
    await run_migrations(config)
    engine = create_engine(config)
    store = SqlAlchemyLookupStore(create_session_factory(engine))
    try:
        leases = await asyncio.gather(
            store.try_acquire_lease("response:rdap:v1", "example.com", "worker-1", timedelta(milliseconds=50)),
            store.try_acquire_lease("response:rdap:v1", "example.com", "worker-2", timedelta(milliseconds=50)),
        )
        acquired = [lease for lease in leases if lease is not None]
        assert len(acquired) == 1
        first = acquired[0]
        await asyncio.sleep(0.1)
        replacement = await store.try_acquire_lease(
            "response:rdap:v1",
            "example.com",
            "replacement-worker",
            timedelta(seconds=30),
        )
        assert replacement is not None
        await store.release_lease(first)
        assert await store.try_acquire_lease(
            "response:rdap:v1",
            "example.com",
            "third-worker",
            timedelta(seconds=30),
        ) is None
        await store.release_lease(replacement)
    finally:
        await engine.dispose()
        await clean_project_schema(config)
