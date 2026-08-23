from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import run_migrations
from tests.database import sqlite_database


@pytest.mark.asyncio
async def test_notification_rule_create_and_list(tmp_path: Path) -> None:
    database = tmp_path / "notifications.db"
    await run_migrations(sqlite_database(database))
    settings = Settings(_env_file=None, database_type="sqlite", database_path=str(database), jwt_secret_key="x", refresh_token_pepper="y", registration_enabled=True)
    with TestClient(create_app(settings)) as client:
        registered = client.post("/api/v1/auth/register", json={"username": "notifier", "password": "123456"})
        headers = {"Authorization": f"Bearer {registered.json()['tokens']['access_token']}"}
        created = client.post("/api/v1/notification-rules", json={"event_type": "expiration", "days_before": 30, "channel": "webhook", "webhook_url": "https://hooks.example.test/domain"}, headers=headers)
        assert created.status_code == 201
        assert created.json()["webhook_url"] == "https://hooks.example.test/domain"
        listed = client.get("/api/v1/notification-rules", headers=headers)
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [created.json()["id"]]
