from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from domainsmanager_application.auth import (
    AuditEvent,
    RefreshTokenRecord,
    SessionRecord,
    UserRecord,
)
from domainsmanager_persistence.auth import (
    ConcurrentUpdateError,
    SqlAlchemyUnitOfWorkFactory,
)
from domainsmanager_persistence.db import (
    create_engine,
    create_session_factory,
    run_migrations,
)
from domainsmanager_persistence.models import SecurityAuditEvent
from tests.database import sqlite_database

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_user(username: str = "Test.User") -> UserRecord:
    return UserRecord(
        id=uuid4(),
        username=username,
        username_normalized=username.casefold(),
        password_hash="hash",
        email=None,
        role="user",
        preferences={},
        is_active=True,
        banned_at=None,
        password_changed_at=NOW,
        last_login_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


async def make_uow(tmp_path: Path):
    database = sqlite_database(tmp_path / "repository.db")
    await run_migrations(database)
    engine = create_engine(database)
    return engine, SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))


@pytest.mark.asyncio
@pytest.mark.integration
async def test_unit_of_work_commits_user_and_audit_together(tmp_path: Path) -> None:
    engine, factory = await make_uow(tmp_path)
    user = make_user()

    async with factory() as uow:
        await uow.users.add(user)
        await uow.audits.add(
            AuditEvent(
                event_type="user.created",
                occurred_at=NOW,
                actor_user_id=user.id,
                target_type="user",
                target_id=user.id,
                request_id="request-1234",
            )
        )
        await uow.commit()

    async with factory() as uow:
        loaded = await uow.users.get_by_username("test.user")
        assert loaded is not None
        assert loaded.created_at.tzinfo is not None
        assert loaded.password_changed_at.tzinfo is not None

    sessions = create_session_factory(engine)
    async with sessions() as session:
        count = await session.scalar(
            select(func.count()).select_from(SecurityAuditEvent)
        )
    await engine.dispose()

    assert count == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_unit_of_work_rolls_back_without_commit(tmp_path: Path) -> None:
    engine, factory = await make_uow(tmp_path)

    async with factory() as uow:
        await uow.users.add(make_user())

    async with factory() as uow:
        assert await uow.users.count() == 0

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_session_token_lifecycle_and_single_consumption(tmp_path: Path) -> None:
    engine, factory = await make_uow(tmp_path)
    user = make_user()
    auth_session = SessionRecord(
        id=uuid4(),
        user_id=user.id,
        created_at=NOW,
        last_seen_at=NOW,
        absolute_expires_at=NOW + timedelta(days=30),
        revoked_at=None,
        revoke_reason=None,
        ip_hash=None,
        user_agent="test-client",
    )
    token = RefreshTokenRecord(
        id=uuid4(),
        session_id=auth_session.id,
        parent_token_id=None,
        replaced_by_token_id=None,
        token_hash=b"a" * 32,
        issued_at=NOW,
        expires_at=NOW + timedelta(days=30),
        consumed_at=None,
        revoked_at=None,
    )
    replacement_id = uuid4()

    async with factory() as uow:
        await uow.users.add(user)
        await uow.sessions.add_session(auth_session)
        await uow.sessions.add_token(token)
        await uow.sessions.add_token(
            RefreshTokenRecord(
                id=replacement_id,
                session_id=auth_session.id,
                parent_token_id=token.id,
                replaced_by_token_id=None,
                token_hash=b"b" * 32,
                issued_at=NOW,
                expires_at=NOW + timedelta(days=30),
                consumed_at=None,
                revoked_at=None,
            )
        )
        await uow.sessions.consume_token(token.id, replacement_id, NOW)
        await uow.commit()

    async with factory() as uow:
        consumed = await uow.sessions.get_token(token.id)
        assert consumed is not None
        assert consumed.consumed_at == NOW
        assert consumed.replaced_by_token_id == replacement_id
        with pytest.raises(ConcurrentUpdateError):
            await uow.sessions.consume_token(token.id, uuid4(), NOW)

    async with factory() as uow:
        await uow.sessions.revoke_session(auth_session.id, NOW, "logout")
        await uow.commit()

    async with factory() as uow:
        loaded_session = await uow.sessions.get_session(auth_session.id)
        loaded_replacement = await uow.sessions.get_token(replacement_id)
        assert loaded_session is not None
        assert loaded_session.revoked_at == NOW
        assert loaded_session.revoke_reason == "logout"
        assert loaded_replacement is not None
        assert loaded_replacement.revoked_at == NOW

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_password_update_and_other_session_revocation(tmp_path: Path) -> None:
    engine, factory = await make_uow(tmp_path)
    user = make_user()
    current = SessionRecord(
        id=uuid4(),
        user_id=user.id,
        created_at=NOW,
        last_seen_at=NOW,
        absolute_expires_at=NOW + timedelta(days=30),
        revoked_at=None,
        revoke_reason=None,
        ip_hash=None,
        user_agent=None,
    )
    other = SessionRecord(
        id=uuid4(),
        user_id=user.id,
        created_at=NOW,
        last_seen_at=NOW,
        absolute_expires_at=NOW + timedelta(days=30),
        revoked_at=None,
        revoke_reason=None,
        ip_hash=None,
        user_agent=None,
    )
    changed_at = NOW + timedelta(hours=1)

    async with factory() as uow:
        await uow.users.add(user)
        await uow.sessions.add_session(current)
        await uow.sessions.add_session(other)
        await uow.users.update_password(user.id, "new-hash", changed_at)
        await uow.sessions.revoke_other_sessions(
            user.id,
            current.id,
            changed_at,
            "password_changed",
        )
        await uow.commit()

    async with factory() as uow:
        loaded_user = await uow.users.get_by_id(user.id)
        loaded_current = await uow.sessions.get_session(current.id)
        loaded_other = await uow.sessions.get_session(other.id)
        assert loaded_user is not None
        assert loaded_user.password_hash == "new-hash"
        assert loaded_user.password_changed_at == changed_at
        assert loaded_current is not None and loaded_current.revoked_at is None
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_system_state_claim_is_unique_and_keeps_transaction_usable(
    tmp_path: Path,
) -> None:
    engine, factory = await make_uow(tmp_path)

    async with factory() as uow:
        assert await uow.system_state.try_claim("bootstrap_admin", NOW) is True
        await uow.commit()

    user = make_user("after-claim")
    async with factory() as uow:
        assert await uow.system_state.try_claim("bootstrap_admin", NOW) is False
        await uow.users.add(user)
        await uow.commit()

    async with factory() as uow:
        assert await uow.users.get_by_id(user.id) is not None

    await engine.dispose()
