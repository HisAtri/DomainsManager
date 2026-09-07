from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import MetaData, Table, select, text

from domainsmanager_persistence.db import (
    create_engine,
    create_session_factory,
    downgrade_migrations,
    run_migrations,
)
from domainsmanager_persistence.models import (
    AppUser,
    DomainRefreshTask,
    IdempotencyRecord,
    ManagedDomain,
)
from tests.database import sqlite_database


async def test_upgrade_preserves_and_merges_existing_refresh_requests(tmp_path):
    config = sqlite_database(tmp_path / "legacy.db")
    await run_migrations(config, "f3a4b5c6d7e8")
    engine = create_engine(config)
    sessions = create_session_factory(engine)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    user_id, domain_id, survivor_id, duplicate_id = [uuid4() for _ in range(4)]
    try:
        async with sessions() as session, session.begin():
            session.add(
                AppUser(
                    id=user_id,
                    username="legacy",
                    username_normalized="legacy",
                    password_hash="hash",
                    password_changed_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                ManagedDomain(
                    id=domain_id,
                    user_id=user_id,
                    name_ascii="example.com",
                    name_unicode="example.com",
                    registrable_domain="example.com",
                    public_suffix="com",
                    tld="com",
                    monitor_enabled=True,
                    created_at=now,
                    updated_at=now,
                    next_check_at=now,
                )
            )
        async with engine.begin() as connection:
            legacy_tasks = await connection.run_sync(
                lambda conn: Table(
                    "domain_refresh_task", MetaData(), autoload_with=conn
                )
            )
            for i, task_id in enumerate((survivor_id, duplicate_id)):
                await connection.execute(
                    legacy_tasks.insert().values(
                        id=task_id.hex,
                        user_id=user_id.hex,
                        managed_domain_id=domain_id.hex,
                        status="queued",
                        force_refresh=bool(i),
                        attempt_count=0,
                        max_attempts=5,
                        available_at=now,
                        created_at=now + timedelta(seconds=i),
                        updated_at=now,
                    )
                )
        async with sessions() as session, session.begin():
            for i, task_id in enumerate((survivor_id, duplicate_id)):
                session.add(
                    IdempotencyRecord(
                        id=uuid4(),
                        user_id=user_id,
                        operation="domain_refresh",
                        resource_id=domain_id,
                        key=f"schedule:{i}",
                        request_fingerprint=str(i),
                        task_id=task_id,
                        created_at=now,
                        expires_at=now + timedelta(days=7),
                    )
                )
        await run_migrations(config)
        async with sessions() as session:
            survivor = await session.get(DomainRefreshTask, survivor_id)
            duplicate = await session.get(DomainRefreshTask, duplicate_id)
            assert (
                survivor.status == "queued"
                and survivor.force_refresh
                and survivor.origin == "scheduled"
            )
            assert (
                duplicate.status == "failed"
                and duplicate.error_code == "duplicate_merged"
            )
            records = (await session.execute(select(IdempotencyRecord))).scalars().all()
            assert len(records) == 2 and {record.task_id for record in records} == {
                survivor_id
            }
            assert (await session.execute(text("PRAGMA foreign_key_check"))).all() == []
        await downgrade_migrations(config, "f3a4b5c6d7e8")
        await run_migrations(config)
        async with sessions() as session:
            assert (await session.execute(text("PRAGMA foreign_key_check"))).all() == []
            assert await session.get(DomainRefreshTask, survivor_id) is not None
    finally:
        await engine.dispose()
