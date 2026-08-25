from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import run_migrations
from tests.database import sqlite_database


async def make_client(tmp_path: Path) -> TestClient:
    database = tmp_path / "admin-api.db"
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
                bootstrap_admin_username="admin",
                bootstrap_admin_password="123456",
            )
        )
    )


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", data={"username": username, "password": "123456"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


@pytest.mark.asyncio
@pytest.mark.api
async def test_admin_user_and_domain_access(tmp_path: Path) -> None:
    client = await make_client(tmp_path)
    with client:
        admin = login(client, "admin")
        registered = client.post(
            "/api/v1/auth/register",
            json={"username": "member", "password": "123456"},
        )
        assert registered.status_code == 201
        member_id = registered.json()["user"]["id"]
        member = {
            "Authorization": f"Bearer {registered.json()['tokens']['access_token']}"
        }
        domain = client.post(
            "/api/v1/domains", json={"name": "example.com"}, headers=member
        ).json()["domain"]

        assert client.get("/api/v1/admin/users", headers=member).status_code == 403
        users = client.get("/api/v1/admin/users", headers=admin)
        assert users.status_code == 200
        assert users.json()["total"] == 2
        domains = client.get("/api/v1/admin/domains", headers=admin)
        assert domains.status_code == 200
        assert domains.json()["items"][0]["id"] == domain["id"]
        queued = client.post(
            f"/api/v1/admin/domains/{domain['id']}/refresh",
            headers={**admin, "Idempotency-Key": "admin-refresh-123"},
        )
        assert queued.status_code == 202
        assert queued.json()["domain_id"] == domain["id"]
        checks = client.get(
            f"/api/v1/admin/domain-checks?domain_id={domain['id']}", headers=admin
        )
        assert checks.status_code == 200
        assert checks.json() == {
            "items": [],
            "page": 1,
            "page_size": 20,
            "total": 0,
            "statistics": {"count_by_outcome": {}},
        }

        banned = client.post(
            f"/api/v1/admin/users/{member_id}/ban",
            json={"reason": "test ban"},
            headers=admin,
        )
        assert banned.status_code == 200
        assert banned.json()["status"] == "banned"
        assert client.get("/api/v1/auth/me", headers=member).status_code == 403

        unbanned = client.post(f"/api/v1/admin/users/{member_id}/unban", headers=admin)
        assert unbanned.status_code == 200
        assert unbanned.json()["status"] == "active"
        member = login(client, "member")
        sessions = client.get(
            f"/api/v1/admin/users/{member_id}/sessions", headers=admin
        )
        assert sessions.status_code == 200
        assert sessions.json()["total"] == 2
        session_id = next(
            item["id"]
            for item in sessions.json()["items"]
            if item["revoked_at"] is None
        )
        revoked = client.post(
            f"/api/v1/admin/users/{member_id}/sessions/{session_id}/revoke",
            headers=admin,
        )
        assert revoked.status_code == 200
        assert revoked.json()["revoke_reason"] == "admin_revoked"
        assert client.get("/api/v1/auth/me", headers=member).status_code == 401
