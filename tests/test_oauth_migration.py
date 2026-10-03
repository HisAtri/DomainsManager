from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from domainsmanager_persistence.db import (
    create_engine,
    create_session_factory,
    downgrade_migrations,
    run_migrations,
)
from domainsmanager_persistence.models import AppUser
from tests.database import sqlite_database


@pytest.mark.asyncio
async def test_oauth_migration_preserves_existing_password_accounts(tmp_path):
    database = sqlite_database(tmp_path / "migration.db")
    await run_migrations(database, "3a6f9d2c7b10")
    engine = create_engine(database)
    user_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO app_user (id,username,username_normalized,password_hash,role,totp_enabled,preferences,is_active,password_changed_at,created_at,updated_at) VALUES (:id,'existing','existing','hash','user',false,'{}',true,:now,:now,:now)"
            ),
            {"id": user_id.hex, "now": datetime.now(UTC)},
        )
    await engine.dispose()
    await run_migrations(database)
    engine = create_engine(database)
    async with create_session_factory(engine)() as session:
        user = await session.scalar(select(AppUser).where(AppUser.id == user_id))
        assert user.password_auth_enabled
        assert not user.username_setup_required
        assert user.password_hash == "hash"
    await engine.dispose()
    await downgrade_migrations(database, "3a6f9d2c7b10")
    await run_migrations(database)
