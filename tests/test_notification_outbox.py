from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from domainsmanager_application.notifications import NotificationOutboxService
from domainsmanager_persistence.auth import SqlAlchemyUnitOfWorkFactory
from domainsmanager_persistence.db import (
    create_engine,
    create_session_factory,
    run_migrations,
)
from domainsmanager_persistence.models import (
    AppUser,
    DomainCheck,
    ManagedDomain,
    NotificationOutbox,
    NotificationRule,
)
from tests.database import sqlite_database


@pytest.mark.asyncio
async def test_outbox_delivery_marks_sent_and_retries_with_backoff(tmp_path: Path) -> None:
    database = sqlite_database(tmp_path / "outbox.db")
    await run_migrations(database)
    engine = create_engine(database)
    factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
    now, user_id, domain_id, rule_id, check_id = datetime(2026, 8, 24, tzinfo=UTC), uuid4(), uuid4(), uuid4(), uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(AppUser.__table__.insert().values(id=user_id, username="outbox", username_normalized="outbox", password_hash="hash", email="user@example.test", role="user", preferences={}, is_active=True, password_changed_at=now, created_at=now, updated_at=now))
            await connection.execute(ManagedDomain.__table__.insert().values(id=domain_id, user_id=user_id, name_ascii="example.com", name_unicode="example.com", registrable_domain="example.com", public_suffix="com", tld="com", statuses=[], nameservers=[], monitor_enabled=True, version=1, created_at=now, updated_at=now))
            await connection.execute(NotificationRule.__table__.insert().values(id=rule_id, user_id=user_id, event_type="status_change", channel="webhook", channel_config={"webhook_url": "https://example.test"}, is_enabled=True, created_at=now, updated_at=now))
            await connection.execute(DomainCheck.__table__.insert().values(id=check_id, managed_domain_id=domain_id, checked_at=now, outcome="success", changed_fields=[], is_stale=False, created_at=now))
            for key in ("sent", "failed"):
                await connection.execute(NotificationOutbox.__table__.insert().values(id=uuid4(), notification_rule_id=rule_id, managed_domain_id=domain_id, domain_check_id=check_id, deduplication_key=key, event_type="status_change", payload={"event_type": key}, status="pending", attempt_count=0, available_at=now, created_at=now, updated_at=now))
        async def succeed(_message: object) -> None: pass
        async def fail(_message: object) -> None: raise RuntimeError("delivery failed")
        assert await NotificationOutboxService(unit_of_work=factory, deliver=succeed, clock=lambda: now).run_once("worker")
        assert await NotificationOutboxService(unit_of_work=factory, deliver=fail, clock=lambda: now, max_attempts=2, retry_base_delay=timedelta(minutes=5)).run_once("worker")
        async with engine.connect() as connection:
            rows = (await connection.execute(select(NotificationOutbox.deduplication_key, NotificationOutbox.status, NotificationOutbox.available_at))).all()
        states = {key: (status, available_at) for key, status, available_at in rows}
        assert sorted(status for status, _ in states.values()) == ["pending", "sent"]
        available_at = next(
            available_at
            for status, available_at in states.values()
            if status == "pending"
        )
        assert available_at is not None
        if available_at.tzinfo is None:
            available_at = available_at.replace(tzinfo=UTC)
        assert available_at == now + timedelta(minutes=5)
    finally:
        await engine.dispose()
