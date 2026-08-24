from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import run_migrations
from domainsmanager_persistence.models import (
    DomainCheck,
    NotificationOutbox,
)
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
        rule_id = created.json()["id"]
        updated = client.patch(
            f"/api/v1/notification-rules/{rule_id}",
            json={"enabled": False},
            headers=headers,
        )
        assert updated.status_code == 200
        assert updated.json()["enabled"] is False
        assert client.get(f"/api/v1/notification-rules/{rule_id}", headers=headers).status_code == 200
        deleted = client.delete(f"/api/v1/notification-rules/{rule_id}", headers=headers)
        assert deleted.status_code == 204
        assert client.get(f"/api/v1/notification-rules/{rule_id}", headers=headers).status_code == 404
        assert client.get("/api/v1/notification-rules", headers=headers).json() == []


@pytest.mark.asyncio
async def test_notification_delivery_history_is_owner_scoped(tmp_path: Path) -> None:
    database = tmp_path / "delivery-history.db"
    await run_migrations(sqlite_database(database))
    settings = Settings(_env_file=None, database_type="sqlite", database_path=str(database), jwt_secret_key="x", refresh_token_pepper="y", registration_enabled=True)
    with TestClient(create_app(settings)) as client:
        registered = client.post("/api/v1/auth/register", json={"username": "history", "password": "123456"})
        headers = {"Authorization": f"Bearer {registered.json()['tokens']['access_token']}"}
        domain = client.post("/api/v1/domains", json={"name": "example.com"}, headers=headers).json()["domain"]
        rule = client.post("/api/v1/notification-rules", json={"event_type": "status_change", "channel": "email"}, headers=headers).json()
        from domainsmanager_persistence.db import create_engine
        from uuid import UUID, uuid4
        engine = create_engine(sqlite_database(database))
        now = datetime.now(UTC)
        try:
            async with engine.begin() as connection:
                check_id = uuid4()
                domain_id, rule_id = UUID(domain["id"]), UUID(rule["id"])
                await connection.execute(DomainCheck.__table__.insert().values(id=check_id, managed_domain_id=domain_id, checked_at=now, outcome="success", changed_fields=[], is_stale=False, created_at=now))
                await connection.execute(NotificationOutbox.__table__.insert().values(id=uuid4(), notification_rule_id=rule_id, managed_domain_id=domain_id, domain_check_id=check_id, deduplication_key="history", event_type="status_change", payload={}, status="dead_letter", attempt_count=2, available_at=now, last_error="RuntimeError: delivery failed", created_at=now, updated_at=now))
            response = client.get("/api/v1/notification-rules/deliveries", headers=headers)
        finally:
            await engine.dispose()
        assert response.status_code == 200
        assert response.json()[0]["failure_reason"] == "RuntimeError: delivery failed"
        assert "webhook_url" not in response.json()[0]
