from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from domainsmanager_persistence.db import (
    create_engine,
    downgrade_migrations,
    run_migrations,
)
from domainsmanager_persistence.models import AppUser, AuthSession
from tests.postgres import (
    PROJECT_TABLES,
    assert_dedicated_postgres,
    clean_project_schema,
    postgres_database,
    public_tables,
)

NOW = datetime(2026, 1, 1, 12, 30, 15, 123456, tzinfo=UTC)
OLD_REVISION = "6f0aad6e5b27"
HEAD_REVISION = "d6f4a9b8c2e1"


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.integration
async def test_postgresql_migration_schema_and_types() -> None:
    config = postgres_database()
    await assert_dedicated_postgres(config)
    await clean_project_schema(config)
    try:
        await run_migrations(config, OLD_REVISION)
        engine = create_engine(config)
        user_id = uuid4()
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO app_user "
                        "(id, username, password_hash, preferences, is_active, "
                        "created_at, updated_at) "
                        "VALUES (:id, :username, :password_hash, CAST(:preferences AS jsonb), "
                        ":is_active, :created_at, :updated_at)"
                    ),
                    {
                        "id": user_id,
                        "username": "Existing.User",
                        "password_hash": "old-hash",
                        "preferences": '{"locale":"zh-CN","unicode":"域名"}',
                        "is_active": True,
                        "created_at": NOW,
                        "updated_at": NOW,
                    },
                )
        finally:
            await engine.dispose()

        await run_migrations(config)
        assert await public_tables(config) == PROJECT_TABLES

        engine = create_engine(config)
        try:
            async with engine.connect() as connection:
                revision = await connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
                user = (
                    await connection.execute(
                        select(
                            AppUser.username_normalized,
                            AppUser.preferences,
                            AppUser.password_changed_at,
                        ).where(AppUser.id == user_id)
                    )
                ).one()
                column_types = {
                    row.column_name: row.data_type
                    for row in (
                        await connection.execute(
                            text(
                                "SELECT column_name, data_type "
                                "FROM information_schema.columns "
                                "WHERE table_schema='public' AND table_name='app_user'"
                            )
                        )
                    )
                }
            assert revision == HEAD_REVISION
            assert user.username_normalized == "existing.user"
            assert user.preferences == {"locale": "zh-CN", "unicode": "域名"}
            assert user.password_changed_at == NOW
            assert column_types["preferences"] == "jsonb"
            assert column_types["password_changed_at"] == "timestamp with time zone"
        finally:
            await engine.dispose()

        engine = create_engine(config)
        try:
            session_id = uuid4()
            async with engine.begin() as connection:
                await connection.execute(
                    AuthSession.__table__.insert().values(
                        id=session_id,
                        user_id=user_id,
                        created_at=NOW,
                        last_seen_at=NOW,
                        absolute_expires_at=NOW + timedelta(days=30),
                    )
                )
            async with engine.connect() as connection:
                stored = (
                    await connection.execute(
                        select(AuthSession.created_at).where(
                            AuthSession.id == session_id
                        )
                    )
                ).scalar_one()
            assert stored == NOW
            assert stored.tzinfo is not None
        finally:
            await engine.dispose()

        await downgrade_migrations(config, OLD_REVISION)
        await run_migrations(config)
        assert await public_tables(config) == PROJECT_TABLES
    finally:
        await clean_project_schema(config)
