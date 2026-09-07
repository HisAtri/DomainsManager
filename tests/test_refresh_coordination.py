import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from domainsmanager_application.scheduler import DomainSchedulerService
from domainsmanager_application.tasks import RefreshTaskService, TaskExecutionPolicy
from domainsmanager_lookup import DomainSnapshot, LookupErrorCode, LookupOutcome
from domainsmanager_persistence.auth import SqlAlchemyUnitOfWorkFactory
from domainsmanager_persistence.db import create_engine, create_session_factory
from domainsmanager_persistence.models import (
    AppUser,
    Base,
    DomainCheck,
    DomainRefreshTask,
    ManagedDomain,
    NotificationOutbox,
)
from domainsmanager_persistence.tasks import SqlAlchemyTaskRepository
from tests.database import sqlite_database

NOW = datetime(2026, 9, 7, tzinfo=UTC)
INTERVAL = timedelta(days=7)


@pytest.fixture
async def coordination(tmp_path):
    engine = create_engine(sqlite_database(tmp_path / "coordination.db"))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = create_session_factory(engine)
    user_id, domain_id = uuid4(), uuid4()
    async with sessions() as session, session.begin():
        session.add(
            AppUser(
                id=user_id,
                username="qc",
                username_normalized="qc",
                password_hash="hash",
                password_changed_at=NOW,
                created_at=NOW,
                updated_at=NOW,
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
                created_at=NOW,
                updated_at=NOW,
                next_check_at=NOW - INTERVAL,
            )
        )
    env = SimpleNamespace(
        engine=engine,
        sessions=sessions,
        factory=SqlAlchemyUnitOfWorkFactory(sessions),
        user_id=user_id,
        domain_id=domain_id,
    )
    try:
        yield env
    finally:
        await engine.dispose()


def service(env, lookup=None, clock=lambda: NOW):
    return RefreshTaskService(
        unit_of_work=env.factory,
        lookup=lookup or AsyncMock(),
        clock=clock,
        policy=TaskExecutionPolicy(successful_refresh_ttl=timedelta(0), max_attempts=2),
    )


@pytest.mark.parametrize("state", ["queued", "running"])
async def test_restart_spreads_existing_backlog_even_if_next_cycle_is_future(
    coordination, state
):
    env = coordination
    tasks = service(env)
    task = await tasks.enqueue(
        env.user_id,
        env.domain_id,
        force_refresh=False,
        idempotency_key="scheduled",
        origin="scheduled",
    )
    async with env.sessions() as session, session.begin():
        domain = await session.get(ManagedDomain, env.domain_id)
        domain.next_check_at = NOW + INTERVAL
        row = await session.get(DomainRefreshTask, task.id)
        row.status = state
        if state == "running":
            row.lease_token, row.lease_owner, row.lease_until = (
                uuid4(),
                "dead-worker",
                NOW - timedelta(seconds=1),
            )
    scheduler = DomainSchedulerService(
        unit_of_work=env.factory, clock=lambda: NOW, random_offset_seconds=lambda _: 120
    )
    assert await scheduler.spread_overdue() == 1
    assert await scheduler.spread_overdue() == 0
    recovered = await tasks.get(env.user_id, task.id)
    assert recovered.status == "queued"
    assert recovered.available_at == NOW + timedelta(seconds=120)
    async with env.factory() as uow:
        assert await uow.tasks.claim("worker", NOW, NOW + timedelta(minutes=2)) is None


@pytest.mark.parametrize("scheduler_claims", [False, True])
async def test_success_preserves_randomized_phase_despite_worker_delay(
    coordination, scheduler_claims
):
    env = coordination
    clock = [NOW]
    lookup = AsyncMock()
    lookup.lookup.return_value = [
        LookupOutcome(
            input_name="example.com",
            snapshot=DomainSnapshot(domain="example.com", source="rdap"),
        )
    ]
    tasks = service(env, lookup, clock=lambda: clock[0])
    await tasks.enqueue(
        env.user_id,
        env.domain_id,
        force_refresh=False,
        idempotency_key="scheduled",
        origin="scheduled",
    )
    scheduler = DomainSchedulerService(
        unit_of_work=env.factory,
        clock=lambda: clock[0],
        random_offset_seconds=lambda _: 120,
    )
    assert await scheduler.spread_overdue() == 1
    anchor = NOW + timedelta(seconds=120)
    clock[0] = anchor + timedelta(hours=2)
    if scheduler_claims:
        assert await scheduler.run_once() == 0  # the queued task owns this occurrence
    assert await tasks.run_once("worker")
    async with env.sessions() as session:
        domain = await session.get(ManagedDomain, env.domain_id)
        assert domain.next_check_at.replace(tzinfo=UTC) == anchor + INTERVAL
    clock[0] = anchor + INTERVAL + timedelta(minutes=5)
    assert await scheduler.run_once() == 1
    assert await tasks.run_once("worker")
    async with env.sessions() as session:
        domain = await session.get(ManagedDomain, env.domain_id)
        assert domain.next_check_at.replace(tzinfo=UTC) == anchor + INTERVAL * 2


async def test_endpoint_wait_never_exhausts_tasks_or_records_failed_checks(
    coordination,
):
    env = coordination
    clock = [NOW]
    lookup = AsyncMock()
    tasks = service(env, lookup, clock=lambda: clock[0])
    task = await tasks.enqueue(
        env.user_id, env.domain_id, force_refresh=False, idempotency_key="limited"
    )
    for _ in range(8):
        retry_at = clock[0] + timedelta(minutes=3)
        lookup.lookup.return_value = [
            LookupOutcome(
                input_name="example.com",
                error_code=LookupErrorCode.RATE_LIMITED,
                error_message="endpoint cooldown",
                retry_after=retry_at,
            )
        ]
        assert await tasks.run_once("worker")
        waiting = await tasks.get(env.user_id, task.id)
        assert waiting.status == "queued"
        assert waiting.attempt_count == 0
        assert waiting.available_at == retry_at
        assert not await tasks.run_once("worker")
        clock[0] = retry_at
    async with env.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(DomainCheck)) == 0
        assert (
            await session.scalar(select(func.count()).select_from(NotificationOutbox))
            == 0
        )
    lookup.lookup.return_value = [
        LookupOutcome(
            input_name="example.com",
            snapshot=DomainSnapshot(domain="example.com", source="rdap"),
        )
    ]
    assert await tasks.run_once("worker")
    completed = await tasks.get(env.user_id, task.id)
    assert completed.status == "success"
    assert completed.error_code is None and completed.error_message is None


async def test_manual_merge_expedites_scheduled_work_and_keeps_force_flag(coordination):
    env = coordination
    tasks = service(env)
    first = await tasks.enqueue(
        env.user_id,
        env.domain_id,
        force_refresh=False,
        idempotency_key="scheduled",
        origin="scheduled",
    )
    scheduler = DomainSchedulerService(
        unit_of_work=env.factory,
        clock=lambda: NOW,
        random_offset_seconds=lambda _: 3600,
    )
    await scheduler.spread_overdue()
    merged = await tasks.enqueue(
        env.user_id, env.domain_id, force_refresh=True, idempotency_key="manual"
    )
    assert merged.id == first.id
    assert merged.force_refresh
    assert merged.origin == "manual"
    assert merged.available_at == NOW


@pytest.mark.parametrize("same_key", [False, True])
@pytest.mark.parametrize("preexisting", [False, True])
async def test_concurrent_enqueue_merges_and_preserves_idempotency(
    coordination, same_key, preexisting
):
    env = coordination
    tasks = service(env)
    if preexisting:
        await tasks.enqueue(
            env.user_id, env.domain_id, force_refresh=False, idempotency_key="seed"
        )
    results = await asyncio.gather(
        *(
            tasks.enqueue(
                env.user_id,
                env.domain_id,
                force_refresh=False,
                idempotency_key="same" if same_key else f"key-{i}",
            )
            for i in range(8)
        )
    )
    assert len({task.id for task in results}) == 1
    for i in range(8):
        replay = await tasks.enqueue(
            env.user_id,
            env.domain_id,
            force_refresh=False,
            idempotency_key="same" if same_key else f"key-{i}",
        )
        assert replay.id == results[0].id


async def test_concurrent_workers_only_claim_once(coordination):
    env = coordination
    tasks = service(env)
    await tasks.enqueue(
        env.user_id, env.domain_id, force_refresh=False, idempotency_key="claim"
    )

    async def claim(i):
        async with env.factory() as uow:
            result = await uow.tasks.claim(
                f"worker-{i}", NOW, NOW + timedelta(minutes=1)
            )
            await uow.commit()
            return result

    results = await asyncio.gather(*(claim(i) for i in range(8)))
    assert len([task for task in results if task is not None]) == 1


async def test_failed_idempotency_write_does_not_commit_an_orphan_task(
    coordination, monkeypatch
):
    env = coordination
    monkeypatch.setattr(
        SqlAlchemyTaskRepository,
        "_add_idempotency",
        AsyncMock(side_effect=RuntimeError("injected failure")),
    )
    with pytest.raises(RuntimeError, match="injected failure"):
        await service(env).enqueue(
            env.user_id, env.domain_id, force_refresh=False, idempotency_key="rollback"
        )
    async with env.sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(DomainRefreshTask))
            == 0
        )


@pytest.mark.parametrize("cooldown", [False, True])
async def test_merge_running_or_cooling_task_preserves_execution(
    coordination, cooldown
):
    env = coordination
    tasks = service(env)
    first = await tasks.enqueue(
        env.user_id,
        env.domain_id,
        force_refresh=False,
        idempotency_key="original",
        origin="scheduled",
    )
    async with env.factory() as uow:
        running = await uow.tasks.claim("worker", NOW, NOW + timedelta(minutes=1))
        if cooldown:
            assert await uow.tasks.defer_rate_limited(
                first.id,
                running.lease_token,
                NOW,
                NOW + timedelta(hours=1),
                "rate limit",
            )
        await uow.commit()
    merged = await tasks.enqueue(
        env.user_id, env.domain_id, force_refresh=True, idempotency_key="merged"
    )
    assert merged.id == first.id
    assert merged.status == ("queued" if cooldown else "running")
    assert merged.force_refresh is cooldown
    if cooldown:
        assert merged.available_at == NOW + timedelta(hours=1)
        assert not await tasks.run_once("other-worker")


async def test_fresh_scheduled_task_advances_phase_without_enqueuing_again(
    coordination,
):
    env = coordination
    lookup = AsyncMock()
    tasks = RefreshTaskService(
        unit_of_work=env.factory, lookup=lookup, clock=lambda: NOW
    )
    async with env.sessions() as session, session.begin():
        session.add(
            DomainCheck(
                id=uuid4(),
                managed_domain_id=env.domain_id,
                checked_at=NOW - timedelta(minutes=1),
                outcome="success",
                snapshot={"domain": "example.com"},
                created_at=NOW,
            )
        )
    queued = await tasks.enqueue(
        env.user_id,
        env.domain_id,
        force_refresh=False,
        idempotency_key="fresh",
        origin="scheduled",
    )
    assert await tasks.run_once("worker")
    assert (await tasks.get(env.user_id, queued.id)).status == "info"
    lookup.lookup.assert_not_awaited()
    scheduler = DomainSchedulerService(unit_of_work=env.factory, clock=lambda: NOW)
    assert await scheduler.run_once() == 0
    async with env.sessions() as session:
        domain = await session.get(ManagedDomain, env.domain_id)
        assert domain.next_check_at.replace(tzinfo=UTC) == NOW + INTERVAL
