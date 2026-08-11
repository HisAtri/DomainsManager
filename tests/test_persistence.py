from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from domainsmanager_lookup.store import StoredLookupRecord
from domainsmanager_persistence.lookup_store import SqlAlchemyLookupStore
from domainsmanager_persistence.models import Base

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sqlalchemy_lookup_store_round_trip_and_head_order(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'store.db'}")
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
    await store.publish(older)

    loaded = await store.get_current(newer.namespace, newer.cache_key)
    assert loaded is not None
    assert loaded.record_id == newer.record_id

    lease = await store.try_acquire_lease(
        newer.namespace, newer.cache_key, "worker", timedelta(seconds=30)
    )
    assert lease is not None
    assert await store.try_acquire_lease(
        newer.namespace, newer.cache_key, "other", timedelta(seconds=30)
    ) is None
    await store.release_lease(lease)
    await engine.dispose()
