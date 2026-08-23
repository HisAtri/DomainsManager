from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from domainsmanager_application.auth import UserRecord
from domainsmanager_application.domains import ManagedDomainRecord
from domainsmanager_application.tasks import RefreshTaskRecord, TaskExecutionPolicy
from domainsmanager_persistence.auth import SqlAlchemyUnitOfWorkFactory
from domainsmanager_persistence.db import (
    create_engine,
    create_session_factory,
    run_migrations,
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
async def test_task_heartbeat_and_retry_keep_the_task_recoverable(tmp_path: Path) -> None:
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
