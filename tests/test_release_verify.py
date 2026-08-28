import json

import pytest

from domainsmanager_api.release_verify import run
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import run_migrations
from tests.database import sqlite_database


def settings_for(database) -> Settings:
    return Settings(
        _env_file=None,
        database_type="sqlite",
        database_path=str(database),
        jwt_secret_key="x",
        refresh_token_pepper="y",
    )


@pytest.mark.asyncio
async def test_release_preflight_fails_for_unmigrated_database(tmp_path) -> None:
    messages: list[str] = []

    assert await run(settings_for(tmp_path / "empty.db"), output=messages.append) == 1
    assert json.loads(messages[0]) == {
        "event": "release_preflight",
        "ready": False,
        "reason": "database_not_ready",
    }


@pytest.mark.asyncio
async def test_release_preflight_reports_safe_operational_snapshot(tmp_path) -> None:
    database = tmp_path / "ready.db"
    await run_migrations(sqlite_database(database))
    messages: list[str] = []

    assert await run(settings_for(database), output=messages.append) == 0
    event = json.loads(messages[0])
    assert event["event"] == "release_preflight"
    assert event["ready"] is True
    assert event["alerts"] == []
    assert event["operational_metrics"]["refresh_tasks_queued"] == 0
    assert "password" not in messages[0]
