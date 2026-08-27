from pathlib import Path

import pytest
from sqlalchemy import inspect

from domainsmanager_persistence.db import (
    create_engine,
    downgrade_migrations,
    run_migrations,
)
from tests.database import sqlite_database


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_query_indexes_are_packaged_in_migrations(tmp_path: Path) -> None:
    database = sqlite_database(tmp_path / "admin-indexes.db")
    await run_migrations(database, "a9e8f7d6c5b4")
    await run_migrations(database)
    engine = create_engine(database)
    try:
        async with engine.connect() as connection:
            domain_indexes = await connection.run_sync(
                lambda sync_connection: {
                    item["name"]
                    for item in inspect(sync_connection).get_indexes("managed_domain")
                }
            )
            check_indexes = await connection.run_sync(
                lambda sync_connection: {
                    item["name"]
                    for item in inspect(sync_connection).get_indexes("domain_check")
                }
            )
        assert {
            "ix_managed_domain_admin_list",
            "ix_managed_domain_admin_owner_created",
        } <= domain_indexes
        assert "ix_domain_check_admin_checked" in check_indexes
    finally:
        await engine.dispose()
    await downgrade_migrations(database, "a9e8f7d6c5b4")
    await run_migrations(database)
