from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from domainsmanager_persistence.db import create_engine, run_migrations
from domainsmanager_persistence.models import AppUser, AuthRefreshToken, AuthSession

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
OLD_REVISION = "6f0aad6e5b27"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_auth_migration_backfills_existing_user(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    url = f"sqlite+aiosqlite:///{database}"
    await run_migrations(url, OLD_REVISION)

    engine = create_engine(url)
    user_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO app_user "
                "(id, username, password_hash, preferences, is_active, "
                "created_at, updated_at) "
                "VALUES (:id, :username, :password_hash, :preferences, "
                ":is_active, :created_at, :updated_at)"
            ),
            {
                "id": user_id.hex,
                "username": "Existing.User",
                "password_hash": "old-hash",
                "preferences": "{}",
                "is_active": True,
                "created_at": NOW,
                "updated_at": NOW,
            },
        )
    await engine.dispose()

    await run_migrations(url)

    engine = create_engine(url)
    async with engine.connect() as connection:
        result = await connection.execute(
            select(
                AppUser.username_normalized,
                AppUser.role,
                AppUser.totp_enabled,
                AppUser.password_changed_at,
            ).where(AppUser.id == user_id)
        )
        row = result.one()
    await engine.dispose()

    assert row.username_normalized == "existing.user"
    assert row.role == "user"
    assert row.totp_enabled is False
    assert row.password_changed_at.replace(tzinfo=timezone.utc) == NOW


@pytest.mark.asyncio
@pytest.mark.integration
async def test_auth_migration_rejects_casefolded_username_conflicts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "conflict.db"
    url = f"sqlite+aiosqlite:///{database}"
    await run_migrations(url, OLD_REVISION)
    engine = create_engine(url)
    async with engine.begin() as connection:
        for username in ("CaseUser", "caseuser"):
            await connection.execute(
                text(
                    "INSERT INTO app_user "
                    "(id, username, password_hash, preferences, is_active, "
                    "created_at, updated_at) "
                    "VALUES (:id, :username, :password_hash, :preferences, "
                    ":is_active, :created_at, :updated_at)"
                ),
                {
                    "id": uuid4().hex,
                    "username": username,
                    "password_hash": "old-hash",
                    "preferences": "{}",
                    "is_active": True,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            )
    await engine.dispose()

    with pytest.raises(
        RuntimeError,
        match="case-insensitive username conflicts",
    ):
        await run_migrations(url)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_auth_schema_enforces_user_constraints(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'constraints.db'}"
    await run_migrations(url)
    engine = create_engine(url)

    invalid_role = AppUser(
        username="invalid-role",
        username_normalized="invalid-role",
        password_hash="hash",
        role="owner",
        password_changed_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    from sqlalchemy.ext.asyncio import async_sessionmaker

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(invalid_role)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        invalid_totp = AppUser(
            username="invalid-totp",
            username_normalized="invalid-totp",
            password_hash="hash",
            role="user",
            totp_enabled=True,
            password_changed_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(invalid_totp)
        with pytest.raises(IntegrityError):
            await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_deleting_user_cascades_auth_sessions_and_tokens(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'cascade.db'}"
    await run_migrations(url)
    engine = create_engine(url)
    from sqlalchemy.ext.asyncio import async_sessionmaker

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid4()
    session_id = uuid4()
    token_id = uuid4()
    async with sessions() as session, session.begin():
        session.add(
            AppUser(
                id=user_id,
                username="cascade-user",
                username_normalized="cascade-user",
                password_hash="hash",
                role="user",
                password_changed_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add(
            AuthSession(
                id=session_id,
                user_id=user_id,
                created_at=NOW,
                last_seen_at=NOW,
                absolute_expires_at=NOW + timedelta(days=30),
            )
        )
        await session.flush()
        session.add(
            AuthRefreshToken(
                id=token_id,
                session_id=session_id,
                token_hash=b"x" * 32,
                issued_at=NOW,
                expires_at=NOW + timedelta(days=30),
            )
        )

    async with sessions() as session, session.begin():
        user = await session.get(AppUser, user_id)
        assert user is not None
        await session.delete(user)

    async with sessions() as session:
        assert await session.get(AuthSession, session_id) is None
        assert await session.get(AuthRefreshToken, token_id) is None

    await engine.dispose()
