from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from domainsmanager_application.auth import UserRecord
from domainsmanager_application.domains import ManagedDomainRecord
from domainsmanager_application.scheduler import DomainSchedulerService, SchedulerPolicy
from domainsmanager_application.tasks import RefreshTaskRecord
from domainsmanager_persistence.auth import SqlAlchemyUnitOfWorkFactory
from domainsmanager_persistence.db import (
    create_engine,
    create_session_factory,
    run_migrations,
)
from domainsmanager_persistence.models import ManagedDomain
from tests.postgres import clean_project_schema, postgres_database


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.integration
@pytest.mark.concurrency
async def test_concurrent_workers_claim_one_refresh_task_and_recover_expired_lease() -> None:
    config = postgres_database()
    await clean_project_schema(config)
    await run_migrations(config)
    engine = create_engine(config)
    factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
    now, user_id, domain_id, task_id = datetime(2026, 8, 24, tzinfo=UTC), uuid4(), uuid4(), uuid4()
    try:
        async with factory() as uow:
            await uow.users.add(UserRecord(user_id, "task-owner", "task-owner", "hash", None, "user", {}, True, None, now, None, now, now))
            await uow.domains.add(ManagedDomainRecord(domain_id, user_id, "example.com", "example.com", "example.com", "com", "com", True, None, None, None, None, None, 1, now, now, None))
            await uow.tasks.add(RefreshTaskRecord(task_id, user_id, domain_id, "example.com", "queued", False, 0, now, now, None, None, None, None, None, now), "claim-test", "0" * 64, now + timedelta(days=1))
            await uow.commit()

        async def claim(worker_id: str):
            async with factory() as uow:
                task = await uow.tasks.claim(worker_id, now, now + timedelta(seconds=1))
                await uow.commit()
                return task

        first, second = await asyncio.gather(claim("worker-a"), claim("worker-b"))
        claimed = [task for task in (first, second) if task is not None]
        assert len(claimed) == 1
        assert claimed[0].id == task_id

        async with factory() as uow:
            replacement = await uow.tasks.claim(
                "worker-recovery", now + timedelta(seconds=2), now + timedelta(minutes=1)
            )
            await uow.commit()
        assert replacement is not None
        assert replacement.id == task_id
        assert replacement.attempt_count == 2
    finally:
        await engine.dispose()
        await clean_project_schema(config)


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.integration
@pytest.mark.concurrency
async def test_concurrent_schedulers_enqueue_a_due_domain_once() -> None:
    config = postgres_database()
    await clean_project_schema(config)
    await run_migrations(config)
    engine = create_engine(config)
    factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
    now, user_id, domain_id = datetime(2026, 8, 24, tzinfo=UTC), uuid4(), uuid4()
    try:
        async with factory() as uow:
            await uow.users.add(UserRecord(user_id, "scheduler-owner", "scheduler-owner", "hash", None, "user", {}, True, None, now, None, now, now))
            await uow.domains.add(ManagedDomainRecord(domain_id, user_id, "example.com", "example.com", "example.com", "com", "com", True, None, None, None, None, None, 1, now, now, None))
            await uow.commit()
        async with engine.begin() as connection:
            await connection.execute(
                ManagedDomain.__table__.update()
                .where(ManagedDomain.id == domain_id)
                .values(next_check_at=now - timedelta(seconds=1))
            )
        scheduler = DomainSchedulerService(
            unit_of_work=factory,
            clock=lambda: now,
            policy=SchedulerPolicy(check_interval=timedelta(hours=1), batch_size=10),
        )
        counts = await asyncio.gather(scheduler.run_once(), scheduler.run_once())
        assert sorted(counts) == [0, 1]
    finally:
        await engine.dispose()
        await clean_project_schema(config)
