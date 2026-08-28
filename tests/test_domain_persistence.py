from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, text

from domainsmanager_persistence.db import (
    create_engine,
    downgrade_migrations,
    run_migrations,
)
from domainsmanager_persistence.models import AppUser, ManagedDomain
from tests.database import sqlite_database

NOW = datetime(2026, 1, 1, tzinfo=UTC)
PREVIOUS_REVISION = "b3c8172a6d1e"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_managed_domain_soft_delete_migration_round_trip(tmp_path: Path) -> None:
    database = sqlite_database(tmp_path / "domain-migration.db")
    await run_migrations(database, PREVIOUS_REVISION)
    engine = create_engine(database)
    user_id = uuid4()
    domain_id = uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                AppUser.__table__.insert().values(
                    id=user_id,
                    username="migration-user",
                    username_normalized="migration-user",
                    password_hash="hash",
                    role="user",
                    password_changed_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO managed_domain "
                    "(id, user_id, name_ascii, name_unicode, registrable_domain, "
                    "public_suffix, tld, statuses, nameservers, monitor_enabled, "
                    "version, created_at, updated_at) VALUES "
                    "(:id, :user_id, :name_ascii, :name_unicode, "
                    ":registrable_domain, :public_suffix, :tld, :statuses, "
                    ":nameservers, :monitor_enabled, :version, :created_at, :updated_at)"
                ),
                {
                    "id": domain_id.hex,
                    "user_id": user_id.hex,
                    "name_ascii": "example.com",
                    "name_unicode": "example.com",
                    "registrable_domain": "example.com",
                    "public_suffix": "com",
                    "tld": "com",
                    "statuses": "[]",
                    "nameservers": "[]",
                    "monitor_enabled": True,
                    "version": 1,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            )
    finally:
        await engine.dispose()

    await run_migrations(database)
    engine = create_engine(database)
    try:
        async with engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {
                    item["name"]
                    for item in inspect(sync_connection).get_columns("managed_domain")
                }
            )
            deleted_at = await connection.scalar(
                select(ManagedDomain.deleted_at).where(ManagedDomain.id == domain_id)
            )
            assert {
                "deleted_at",
                "deleted_by_user_id",
                "registry_expires_at",
                "registrar_expires_at",
                "expiration_status",
            } <= columns
            assert deleted_at is None
    finally:
        await engine.dispose()

    await downgrade_migrations(database, PREVIOUS_REVISION)
    await run_migrations(database)
