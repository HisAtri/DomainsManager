from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import run_migrations
from tests.database import sqlite_database


async def make_client(tmp_path: Path) -> TestClient:
    database = tmp_path / "tasks-api.db"
    await run_migrations(sqlite_database(database))
    return TestClient(
        create_app(
            Settings(
                _env_file=None,
                database_type="sqlite",
                database_path=str(database),
                jwt_secret_key="x",
                refresh_token_pepper="y",
                registration_enabled=True,
            )
        )
    )


def user_headers(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "123456"},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


@pytest.mark.asyncio
@pytest.mark.api
async def test_refresh_task_is_idempotent_and_owner_scoped(tmp_path: Path) -> None:
    client = await make_client(tmp_path)
    with client:
        first = user_headers(client, "task-owner")
        second = user_headers(client, "task-other")
        domain = client.post(
            "/api/v1/domains", json={"name": "example.com"}, headers=first
        ).json()["domain"]
        key = "request-key-1234"
        queued = client.post(
            f"/api/v1/domains/{domain['id']}/refresh",
            json={"force_refresh": False},
            headers={**first, "Idempotency-Key": key},
        )
        assert queued.status_code == 202
        assert queued.headers["Location"].endswith(
            f"/api/v1/tasks/{queued.json()['id']}"
        )
        assert queued.json()["status"] == "queued"

        repeated = client.post(
            f"/api/v1/domains/{domain['id']}/refresh",
            json={"force_refresh": False},
            headers={**first, "Idempotency-Key": key},
        )
        assert repeated.status_code == 202
        assert repeated.json()["id"] == queued.json()["id"]

        conflicting = client.post(
            f"/api/v1/domains/{domain['id']}/refresh",
            json={"force_refresh": True},
            headers={**first, "Idempotency-Key": key},
        )
        assert conflicting.status_code == 409
        assert conflicting.json()["code"] == "idempotency_conflict"

        task = client.get(f"/api/v1/tasks/{queued.json()['id']}", headers=first)
        assert task.status_code == 200
        assert task.headers["Retry-After"] == "2"
        assert (
            client.get(
                f"/api/v1/tasks/{queued.json()['id']}", headers=second
            ).status_code
            == 404
        )
        checks = client.get(f"/api/v1/domains/{domain['id']}/checks", headers=first)
        assert checks.status_code == 200
        assert checks.json()["items"] == []
