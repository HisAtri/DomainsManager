from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from domainsmanager_application.auth import UserRecord
from domainsmanager_application.domains import ManagedDomainRecord
from domainsmanager_application.scheduler import DomainSchedulerService, SchedulerPolicy
from domainsmanager_persistence.auth import SqlAlchemyUnitOfWorkFactory
from domainsmanager_persistence.db import (
    create_engine,
    create_session_factory,
    run_migrations,
)
from domainsmanager_persistence.models import DomainRefreshTask, ManagedDomain
from tests.database import sqlite_database


@pytest.mark.asyncio
async def test_scheduler_enqueues_due_domains_once(tmp_path: Path) -> None:
    database = sqlite_database(tmp_path / "scheduler.db")
    await run_migrations(database)
    engine = create_engine(database)
    factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
    now = datetime(2026, 8, 24, tzinfo=UTC)
    due_domain_id = uuid4()
    disabled_domain_id = uuid4()
    user_id = uuid4()
    try:
        async with factory() as uow:
            await uow.users.add(
                UserRecord(
                    id=user_id,
                    username="scheduler-user",
                    username_normalized="scheduler-user",
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
            for domain_id, name, monitor_enabled in (
                (due_domain_id, "example.com", True),
                (disabled_domain_id, "example.net", False),
            ):
                await uow.domains.add(
                    ManagedDomainRecord(
                        id=domain_id,
                        user_id=user_id,
                        name_ascii=name,
                        name_unicode=name,
                        registrable_domain=name,
                        public_suffix="com",
                        tld="com",
                        monitor_enabled=monitor_enabled,
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
        async with engine.begin() as connection:
            await connection.execute(
                update(ManagedDomain)
                .where(ManagedDomain.id.in_((due_domain_id, disabled_domain_id)))
                .values(next_check_at=now - timedelta(minutes=1))
            )

        scheduler = DomainSchedulerService(
            unit_of_work=factory,
            clock=lambda: now,
            policy=SchedulerPolicy(check_interval=timedelta(hours=12), batch_size=10),
        )
        assert await scheduler.run_once() == 1
        assert await scheduler.run_once() == 0

        async with engine.connect() as connection:
            tasks = (
                await connection.execute(
                    select(
                        DomainRefreshTask.managed_domain_id, DomainRefreshTask.status
                    ).order_by(DomainRefreshTask.created_at)
                )
            ).all()
            due_next_check = await connection.scalar(
                select(ManagedDomain.next_check_at).where(
                    ManagedDomain.id == due_domain_id
                )
            )
            disabled_next_check = await connection.scalar(
                select(ManagedDomain.next_check_at).where(
                    ManagedDomain.id == disabled_domain_id
                )
            )
        assert len(tasks) == 1
        assert tasks[0].managed_domain_id == due_domain_id
        assert tasks[0].status == "queued"
        if due_next_check is not None and due_next_check.tzinfo is None:
            due_next_check = due_next_check.replace(tzinfo=UTC)
        if disabled_next_check is not None and disabled_next_check.tzinfo is None:
            disabled_next_check = disabled_next_check.replace(tzinfo=UTC)
        assert due_next_check == now + timedelta(hours=12)
        assert disabled_next_check == now - timedelta(minutes=1)
    finally:
        await engine.dispose()
