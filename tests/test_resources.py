from pathlib import Path

import pytest

from domainsmanager_api.resources import create_resources
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import run_migrations
from tests.database import sqlite_database


@pytest.mark.asyncio
async def test_database_ready_requires_current_migration(tmp_path: Path) -> None:
    database = tmp_path / "ready.db"
    settings = Settings(
        _env_file=None,
        database_type="sqlite",
        database_path=str(database),
        jwt_secret_key="x",
        refresh_token_pepper="y",
    )
    resources = create_resources(settings)
    try:
        assert not await resources.database_ready()
        await run_migrations(sqlite_database(database))
        assert await resources.database_ready()
    finally:
        await resources.close()
