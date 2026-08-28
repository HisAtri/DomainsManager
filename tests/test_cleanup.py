from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from domainsmanager_api.cleanup import CleanupResult, run_cleanup
from domainsmanager_persistence.db import (
    create_engine,
    create_session_factory,
    run_migrations,
)
from domainsmanager_persistence.models import AppUser, AuthSession, SecurityAuditEvent
from tests.database import sqlite_database


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cleanup_is_dry_run_until_apply(tmp_path) -> None:
    database = sqlite_database(tmp_path / "cleanup.db")
    await run_migrations(database)
    engine = create_engine(database)
    sessions = create_session_factory(engine)
    before = datetime(2026, 1, 2, tzinfo=UTC)
    user_id = uuid4()
    expired_session_id = uuid4()
    active_session_id = uuid4()
    try:
        async with sessions.begin() as session:
            session.add(
                AppUser(
                    id=user_id,
                    username="cleanup-user",
                    username_normalized="cleanup-user",
                    password_hash="hash",
                    password_changed_at=before,
                    created_at=before,
                    updated_at=before,
                )
            )
            session.add_all(
                [
                    AuthSession(
                        id=expired_session_id,
                        user_id=user_id,
                        created_at=before - timedelta(days=3),
                        last_seen_at=before - timedelta(days=3),
                        absolute_expires_at=before - timedelta(days=1),
                    ),
                    AuthSession(
                        id=active_session_id,
                        user_id=user_id,
                        created_at=before,
                        last_seen_at=before,
                        absolute_expires_at=before + timedelta(days=1),
                    ),
                    SecurityAuditEvent(
                        id=uuid4(),
                        actor_user_id=user_id,
                        event_type="old_event",
                        event_metadata={},
                        occurred_at=before - timedelta(days=1),
                    ),
                    SecurityAuditEvent(
                        id=uuid4(),
                        actor_user_id=user_id,
                        event_type="new_event",
                        event_metadata={},
                        occurred_at=before,
                    ),
                ]
            )

        dry_run = await run_cleanup(
            sessions, before=before, targets=("sessions", "audit"), apply=False
        )
        assert dry_run == [CleanupResult("sessions", 1), CleanupResult("audit", 1)]

        applied = await run_cleanup(
            sessions, before=before, targets=("sessions", "audit"), apply=True
        )
        assert applied == dry_run
        async with sessions() as session:
            assert await session.get(AuthSession, expired_session_id) is None
            assert await session.get(AuthSession, active_session_id) is not None
            events = (await session.execute(select(SecurityAuditEvent.event_type))).scalars().all()
            assert events == ["new_event"]
    finally:
        await engine.dispose()
