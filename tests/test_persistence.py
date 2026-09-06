from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from domainsmanager_lookup.store import StoredLookupRecord
from domainsmanager_persistence.db import create_engine, run_migrations
from domainsmanager_persistence.lookup_store import SqlAlchemyLookupStore
from domainsmanager_persistence.models import Base
from tests.database import sqlite_database

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sqlalchemy_lookup_store_round_trip_and_head_order(tmp_path: Path) -> None:
    engine = create_engine(sqlite_database(tmp_path / "store.db"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store = SqlAlchemyLookupStore(async_sessionmaker(engine, expire_on_commit=False))

    newer = StoredLookupRecord(
        record_id=uuid4(), namespace="response:rdap:v1", cache_key="example.com",
        schema_version=1, payload=b"new", payload_codec="raw", content_hash="new",
        observed_at=NOW + timedelta(seconds=2), fresh_until=NOW + timedelta(hours=1),
    )
    older = StoredLookupRecord(
        record_id=uuid4(), namespace="response:rdap:v1", cache_key="example.com",
        schema_version=1, payload=b"old", payload_codec="raw", content_hash="old",
        observed_at=NOW + timedelta(seconds=1), fresh_until=NOW + timedelta(hours=1),
    )
    await store.publish(newer)
    await store.publish(newer)
    duplicate = StoredLookupRecord(
        record_id=uuid4(),
        namespace=newer.namespace,
        cache_key=newer.cache_key,
        schema_version=newer.schema_version,
        payload=newer.payload,
        payload_codec=newer.payload_codec,
        content_hash=newer.content_hash,
        observed_at=newer.observed_at,
        fresh_until=newer.fresh_until,
    )
    await store.publish(duplicate)
    await store.publish(older)

    loaded = await store.get_current(newer.namespace, newer.cache_key)
    assert loaded is not None
    assert loaded.record_id == newer.record_id

    conflicting = StoredLookupRecord(
        record_id=newer.record_id,
        namespace=newer.namespace,
        cache_key=newer.cache_key,
        schema_version=1,
        payload=b"conflict",
        payload_codec="raw",
        content_hash="conflict",
        observed_at=newer.observed_at,
        fresh_until=newer.fresh_until,
    )
    with pytest.raises(ValueError, match="different data"):
        await store.publish(conflicting)

    lease = await store.try_acquire_lease(
        newer.namespace, newer.cache_key, "worker", timedelta(seconds=30)
    )
    assert lease is not None
    assert await store.try_acquire_lease(
        newer.namespace, newer.cache_key, "other", timedelta(seconds=30)
    ) is None
    await store.release_lease(lease)
    await engine.dispose()


@pytest.mark.asyncio
async def test_endpoint_gate_state_is_shared_between_store_instances(
    tmp_path: Path,
) -> None:
    database = sqlite_database(tmp_path / "endpoint-gate.db")
    await run_migrations(database)
    engine = create_engine(database)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    first_store = SqlAlchemyLookupStore(sessions)
    second_store = SqlAlchemyLookupStore(sessions)
    lease = await first_store.try_acquire_endpoint_gate(
        "whois", "whois.example:43", "worker-1", timedelta(minutes=1)
    )
    assert lease is not None
    assert (
        await second_store.try_acquire_endpoint_gate(
            "whois", "whois.example:43", "worker-2", timedelta(minutes=1)
        )
        is None
    )

    retry_after = datetime.now(UTC) + timedelta(minutes=2)
    blocked_until = await first_store.block_endpoint_gate(
        lease,
        retry_after=retry_after,
        retry_base=timedelta(minutes=1),
        retry_max=timedelta(hours=1),
    )
    state = await second_store.get_endpoint_gate_state(
        "whois", "whois.example:43"
    )
    assert blocked_until == retry_after
    assert state is not None
    assert state.blocked_until == retry_after
    assert state.failure_count == 1
    await engine.dispose()
