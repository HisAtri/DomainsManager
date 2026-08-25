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
                bootstrap_admin_username="task-admin",
                bootstrap_admin_password="123456",
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
        task_list = client.get("/api/v1/tasks?page=1&page_size=10", headers=first)
        assert task_list.status_code == 200
        assert task_list.json()["total"] == 2
        assert {item["domain_name"] for item in task_list.json()["items"]} == {
            "example.com"
        }
        assert {item["status"] for item in task_list.json()["items"]} == {"queued"}
        assert all(item["result"] is None for item in task_list.json()["items"])


@pytest.mark.asyncio
@pytest.mark.api
async def test_admin_can_override_successful_refresh_ttl(tmp_path: Path) -> None:
    client = await make_client(tmp_path)
    with client:
        login = client.post(
            "/api/v1/auth/login",
            data={"username": "task-admin", "password": "123456"},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}
        assert (
            client.get("/api/v1/admin/settings/refresh-policy", headers=headers).json()[
                "successful_refresh_ttl_seconds"
            ]
            == 1800
        )
        updated = client.patch(
            "/api/v1/admin/settings/refresh-policy",
            json={"successful_refresh_ttl_seconds": 3600},
            headers=headers,
        )
        assert updated.status_code == 200
        assert updated.json()["successful_refresh_ttl_seconds"] == 3600
        settings = client.get("/api/v1/admin/settings", headers=headers)
        assert settings.status_code == 200
        assert settings.json()[0]["version"] == 1
        conflict = client.put(
            "/api/v1/admin/settings/successful_refresh_ttl_seconds",
            json={"value": 7200},
            headers={**headers, "If-Match": "0"},
        )
        assert conflict.status_code == 409
        updated_setting = client.put(
            "/api/v1/admin/settings/successful_refresh_ttl_seconds",
            json={"value": 7200},
            headers={**headers, "If-Match": "1"},
        )
        assert updated_setting.status_code == 200
        assert updated_setting.json()["version"] == 2
