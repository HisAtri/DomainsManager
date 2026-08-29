import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from domainsmanager_application.auth import UserRecord
from domainsmanager_application.domains import ManagedDomainRecord
from domainsmanager_application.tasks import (
    RefreshTaskRecord,
    RefreshTaskService,
    TaskExecutionPolicy,
)
from domainsmanager_lookup import DomainSnapshot, LookupOutcome
from domainsmanager_persistence.auth import SqlAlchemyUnitOfWorkFactory
from domainsmanager_persistence.db import (
    create_engine,
    create_session_factory,
    run_migrations,
)
from domainsmanager_persistence.models import (
    ManagedDomain,
    NotificationOutbox,
    NotificationRule,
)
from tests.database import sqlite_database


def test_task_retry_policy_uses_bounded_exponential_backoff() -> None:
    policy = TaskExecutionPolicy(
        retry_base_delay=timedelta(seconds=10),
        retry_max_delay=timedelta(seconds=25),
    )
    now = datetime(2026, 8, 24, tzinfo=UTC)

    assert policy.retry_at(now, 1) == now + timedelta(seconds=10)
    assert policy.retry_at(now, 2) == now + timedelta(seconds=20)
    assert policy.retry_at(now, 3) == now + timedelta(seconds=25)


@pytest.mark.asyncio
async def test_task_heartbeat_and_retry_keep_the_task_recoverable(
    tmp_path: Path,
) -> None:
    database = sqlite_database(tmp_path / "task-policy.db")
    await run_migrations(database)
    engine = create_engine(database)
    factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
    now = datetime(2026, 8, 24, tzinfo=UTC)
    user_id = uuid4()
    domain_id = uuid4()
    task_id = uuid4()

    async with factory() as uow:
        await uow.users.add(
            UserRecord(
                id=user_id,
                username="task-policy",
                username_normalized="task-policy",
                password_hash="hash",
                email=None,
                role="user",
                preferences={},
                is_active=True,
                banned_at=None,
                password_changed_at=now,
                last_login_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        await uow.domains.add(
            ManagedDomainRecord(
                id=domain_id,
                user_id=user_id,
                name_ascii="example.com",
                name_unicode="example.com",
                registrable_domain="example.com",
                public_suffix="com",
                tld="com",
                monitor_enabled=True,
                renewal_mode=None,
                notes=None,
                expires_at=None,
                last_check_at=None,
                last_outcome=None,
                version=1,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
        )
        await uow.tasks.add(
            RefreshTaskRecord(
                id=task_id,
                user_id=user_id,
                domain_id=domain_id,
                domain_name="example.com",
                status="queued",
                force_refresh=False,
                attempt_count=0,
                created_at=now,
                updated_at=now,
                started_at=None,
                completed_at=None,
                check_id=None,
                error_code=None,
                error_message=None,
                available_at=now,
                max_attempts=2,
            ),
            "idempotency-key",
            "fingerprint",
            now + timedelta(days=1),
        )
        await uow.commit()

    async with factory() as uow:
        claimed = await uow.tasks.claim("worker-1", now, now + timedelta(minutes=2))
        assert claimed is not None and claimed.lease_token is not None
        assert await uow.tasks.heartbeat(
            task_id,
            claimed.lease_token,
            now + timedelta(minutes=3),
            now + timedelta(minutes=1),
        )
        check = await uow.tasks.complete_failure(
            task_id,
            claimed.lease_token,
            now + timedelta(minutes=1),
            10,
            "temporary_failure",
            "upstream unavailable",
            retry_at=now + timedelta(minutes=2),
        )
        assert check is not None
        await uow.commit()

    async with factory() as uow:
        task = await uow.tasks.get(user_id, task_id)
        assert task is not None
        assert task.status == "queued"
        assert task.attempt_count == 1
        assert task.available_at == now + timedelta(minutes=2)
        assert task.completed_at is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_expiration_backfill_queues_unknown_domains(tmp_path: Path) -> None:
    database = sqlite_database(tmp_path / "expiration-backfill.db")
    await run_migrations(database)
    engine = create_engine(database)
    factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
    now = datetime(2026, 8, 24, tzinfo=UTC)
    user_id = uuid4()
    domain_id = uuid4()
    try:
        async with factory() as uow:
            await uow.users.add(
                UserRecord(
                    id=user_id,
                    username="backfill-user",
                    username_normalized="backfill-user",
                    password_hash="hash",
                    email=None,
                    role="user",
                    preferences={},
                    is_active=True,
                    banned_at=None,
                    password_changed_at=now,
                    last_login_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            await uow.domains.add(
                ManagedDomainRecord(
                    id=domain_id,
                    user_id=user_id,
                    name_ascii="example.com",
                    name_unicode="example.com",
                    registrable_domain="example.com",
                    public_suffix="com",
                    tld="com",
                    monitor_enabled=True,
                    renewal_mode=None,
                    notes=None,
                    expires_at=None,
                    last_check_at=None,
                    last_outcome=None,
                    version=1,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            )
            await uow.commit()

        service = RefreshTaskService(unit_of_work=factory, lookup=DelayedLookup([]))
        assert await service.enqueue_expiration_backfill() == 1
        async with factory() as uow:
            tasks = await uow.tasks.list(user_id, 1, 20, None)
        assert len(tasks.items) == 1
        assert tasks.items[0].force_refresh is True
    finally:
        await engine.dispose()


class DelayedLookup:
    def __init__(self, outcomes: list[LookupOutcome], delay: float = 0) -> None:
        self._outcomes = outcomes
        self._delay = delay
        self.started = asyncio.Event()

    async def lookup(self, *_args: object, **_kwargs: object) -> list[LookupOutcome]:
        self.started.set()
        if self._delay:
            await asyncio.sleep(self._delay)
        return [self._outcomes.pop(0)]


def successful_outcome(*, expires_at: datetime) -> LookupOutcome:
    return LookupOutcome(
        input_name="example.com",
        snapshot=DomainSnapshot(
            domain="example.com",
            registrar={"name": "Example Registrar"},
            statuses=["ok"],
            expires_at=expires_at,
            registry_expires_at=expires_at,
            registrar_expires_at=expires_at,
            expiration_status="active",
            expiration_checked_at=expires_at - timedelta(days=1),
            registrar_rdap_url="https://registrar.example/domain/example.com",
            nameservers=["ns1.example.com"],
            dnssec_enabled=True,
            source="rdap",
        ),
    )


@pytest.mark.asyncio
async def test_worker_renews_lease_and_records_snapshot_changes(tmp_path: Path) -> None:
    database = sqlite_database(tmp_path / "task-worker.db")
    await run_migrations(database)
    engine = create_engine(database)
    factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
    now = datetime.now(UTC)
    user_id = uuid4()
    domain_id = uuid4()
    try:
        async with factory() as uow:
            await uow.users.add(
                UserRecord(
                    id=user_id,
                    username="task-worker",
                    username_normalized="task-worker",
                    password_hash="hash",
                    email=None,
                    role="user",
                    preferences={},
                    is_active=True,
                    banned_at=None,
                    password_changed_at=now,
                    last_login_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            await uow.domains.add(
                ManagedDomainRecord(
                    id=domain_id,
                    user_id=user_id,
                    name_ascii="example.com",
                    name_unicode="example.com",
                    registrable_domain="example.com",
                    public_suffix="com",
                    tld="com",
                    monitor_enabled=True,
                    renewal_mode=None,
                    notes=None,
                    expires_at=None,
                    last_check_at=None,
                    last_outcome=None,
                    version=1,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            )
            await uow.commit()
        webhook_rule_id = uuid4()
        async with engine.begin() as connection:
            await connection.execute(
                NotificationRule.__table__.insert().values(
                    id=webhook_rule_id,
                    user_id=user_id,
                    managed_domain_id=domain_id,
                    event_type="domain.status_changed",
                    days_before=None,
                    channel="webhook",
                    webhook_name="Operations endpoint",
                    channel_config={"webhook_url": "https://hooks.example.test/events"},
                    is_enabled=True,
                    created_at=now,
                    updated_at=now,
                )
            )

        lookup = DelayedLookup(
            [
                successful_outcome(expires_at=now + timedelta(days=30)),
                successful_outcome(expires_at=now + timedelta(days=31)),
            ],
            delay=0.2,
        )
        policy = TaskExecutionPolicy(
            lease_duration=timedelta(milliseconds=90),
            successful_check_interval=timedelta(hours=6),
            successful_refresh_ttl=timedelta(0),
        )
        service = RefreshTaskService(unit_of_work=factory, lookup=lookup, policy=policy)
        await service.enqueue(
            user_id, domain_id, force_refresh=False, idempotency_key="first-task-key"
        )
        running = asyncio.create_task(service.run_once("worker-1"))
        await lookup.started.wait()
        await asyncio.sleep(0.11)
        async with factory() as uow:
            assert (
                await uow.tasks.claim(
                    "worker-2",
                    datetime.now(UTC),
                    datetime.now(UTC) + policy.lease_duration,
                )
                is None
            )
        assert await running

        await service.enqueue(
            user_id, domain_id, force_refresh=False, idempotency_key="second-task-key"
        )
        assert await service.run_once("worker-1")

        async with factory() as uow:
            checks = await uow.tasks.list_checks(
                domain_id,
                page=1,
                page_size=10,
                outcome=None,
                checked_from=None,
                checked_to=None,
            )
        assert [check.changed_fields for check in checks.items] == [
            ["expires_at", "registry_expires_at", "registrar_expires_at"],
            [],
        ]
        async with factory() as uow:
            stored = await uow.domains.get(user_id, domain_id)
            assert stored is not None
            assert stored.registry_expires_at == now + timedelta(days=31)
            assert stored.registrar_expires_at == now + timedelta(days=31)
            assert stored.expiration_status == "active"
            assert (
                stored.registrar_rdap_url
                == "https://registrar.example/domain/example.com"
            )
        assert checks.items[0].snapshot is not None
        async with engine.connect() as connection:
            next_check_at = await connection.scalar(
                select(ManagedDomain.next_check_at).where(ManagedDomain.id == domain_id)
            )
            outbox = (
                await connection.execute(
                    select(NotificationOutbox.id, NotificationOutbox.payload).where(
                        NotificationOutbox.event_type == "domain.status_changed"
                    )
                )
            ).one_or_none()
        assert outbox is not None
        outbox_id, payload = outbox
        assert payload["id"] == str(outbox_id)
        assert payload["type"] == "domain.status_changed"
        assert payload["api_version"] == "2026-08-30"
        assert payload["webhook"] == {
            "id": str(webhook_rule_id),
            "name": "Operations endpoint",
        }
        assert payload["data"]["changed_fields"] == [
            "expires_at",
            "registry_expires_at",
            "registrar_expires_at",
        ]
        assert next_check_at is not None
        if next_check_at.tzinfo is None:
            next_check_at = next_check_at.replace(tzinfo=UTC)
        assert next_check_at > datetime.now(UTC) + timedelta(hours=5)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_marks_recent_success_as_info_without_another_lookup(
    tmp_path: Path,
) -> None:
    database = sqlite_database(tmp_path / "task-fresh.db")
    await run_migrations(database)
    engine = create_engine(database)
    factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
    now = datetime(2026, 8, 25, tzinfo=UTC)
    user_id, domain_id = uuid4(), uuid4()
    try:
        async with factory() as uow:
            await uow.users.add(
                UserRecord(
                    id=user_id,
                    username="fresh-user",
                    username_normalized="fresh-user",
                    password_hash="hash",
                    email=None,
                    role="user",
                    preferences={},
                    is_active=True,
                    banned_at=None,
                    password_changed_at=now,
                    last_login_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            await uow.domains.add(
                ManagedDomainRecord(
                    id=domain_id,
                    user_id=user_id,
                    name_ascii="example.com",
                    name_unicode="example.com",
                    registrable_domain="example.com",
                    public_suffix="com",
                    tld="com",
                    monitor_enabled=True,
                    renewal_mode=None,
                    notes=None,
                    expires_at=None,
                    last_check_at=None,
                    last_outcome=None,
                    version=1,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            )
            await uow.commit()
        lookup = DelayedLookup(
            [successful_outcome(expires_at=now + timedelta(days=30))]
        )
        service = RefreshTaskService(
            unit_of_work=factory,
            lookup=lookup,
            clock=lambda: now,
            policy=TaskExecutionPolicy(successful_refresh_ttl=timedelta(minutes=30)),
        )
        await service.enqueue(
            user_id, domain_id, force_refresh=False, idempotency_key="fresh-first-key"
        )
        assert await service.run_once("worker-1")
        second = await service.enqueue(
            user_id, domain_id, force_refresh=True, idempotency_key="fresh-second-key"
        )
        assert await service.run_once("worker-1")
        assert len(lookup._outcomes) == 0
        task = await service.get(user_id, second.id)
        assert task.status == "info"
        assert task.result_code == "data_fresh"
        assert task.result_message == "距上次成功刷新不足30分钟"
        assert task.source_check_id is not None
        assert task.check_id is None
    finally:
        await engine.dispose()
