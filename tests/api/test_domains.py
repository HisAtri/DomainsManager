import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import run_migrations
from tests.database import sqlite_database


async def make_client(tmp_path: Path) -> TestClient:
    database = tmp_path / "domains-api.db"
    await run_migrations(sqlite_database(database))
    settings = Settings(
        _env_file=None,
        database_type="sqlite",
        database_path=str(database),
        jwt_secret_key="x",
        refresh_token_pepper="y",
        registration_enabled=True,
    )
    return TestClient(create_app(settings))


def register(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "123456"},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


@pytest.mark.asyncio
@pytest.mark.api
async def test_domain_crud_soft_delete_restore_and_etag(tmp_path: Path) -> None:
    client = await make_client(tmp_path)
    with client:
        headers = register(client, "domain-user")
        created = client.post(
            "/api/v1/domains",
            json={"name": "BÜCHER.DE", "monitor_enabled": False},
            headers=headers,
        )
        assert created.status_code == 201
        assert created.headers["ETag"] == '"1"'
        domain = created.json()["domain"]
        domain_id = domain["id"]
        assert domain["identity"]["ascii_name"] == "xn--bcher-kva.de"
        assert domain["monitor_enabled"] is False
        assert domain["expiration_status"] == "unknown"
        assert domain["registrar_expires_at"] is None

        listed = client.get("/api/v1/domains", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        missing_precondition = client.patch(
            f"/api/v1/domains/{domain_id}",
            json={"notes": "managed"},
            headers=headers,
        )
        assert missing_precondition.status_code == 428
        assert missing_precondition.json()["code"] == "precondition_required"

        null_monitor = client.patch(
            f"/api/v1/domains/{domain_id}",
            json={"monitor_enabled": None},
            headers={**headers, "If-Match": '"1"'},
        )
        assert null_monitor.status_code == 422

        updated = client.patch(
            f"/api/v1/domains/{domain_id}",
            json={"notes": "managed", "renewal_mode": "manual"},
            headers={**headers, "If-Match": '"1"'},
        )
        assert updated.status_code == 200
        assert updated.headers["ETag"] == '"2"'
        assert updated.json()["notes"] == "managed"

        stale = client.patch(
            f"/api/v1/domains/{domain_id}",
            json={"notes": "stale"},
            headers={**headers, "If-Match": '"1"'},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "version_conflict"

        deleted = client.delete(f"/api/v1/domains/{domain_id}", headers=headers)
        assert deleted.status_code == 204
        assert (
            client.get(f"/api/v1/domains/{domain_id}", headers=headers).status_code
            == 404
        )
        assert client.get("/api/v1/domains", headers=headers).json()["total"] == 0
        assert (
            client.delete(f"/api/v1/domains/{domain_id}", headers=headers).status_code
            == 204
        )

        restored = client.post(
            "/api/v1/domains", json={"name": "xn--bcher-kva.de"}, headers=headers
        )
        assert restored.status_code == 200
        assert restored.json()["domain"]["id"] == domain_id
        assert restored.headers["ETag"] == '"4"'


@pytest.mark.asyncio
@pytest.mark.api
async def test_domain_list_defaults_to_newest_first_with_twenty_items(
    tmp_path: Path,
) -> None:
    client = await make_client(tmp_path)
    with client:
        headers = register(client, "domain-list-user")
        first = client.post(
            "/api/v1/domains", json={"name": "alpha.com"}, headers=headers
        )
        second = client.post(
            "/api/v1/domains", json={"name": "zeta.com"}, headers=headers
        )
        assert first.status_code == 201
        assert second.status_code == 201

        listed = client.get("/api/v1/domains", headers=headers)

        assert listed.status_code == 200
        body = listed.json()
        assert body["page_size"] == 20
        assert [item["identity"]["ascii_name"] for item in body["items"]] == [
            "zeta.com",
            "alpha.com",
        ]


@pytest.mark.asyncio
@pytest.mark.api
async def test_enabling_monitoring_enqueues_an_initial_refresh(tmp_path: Path) -> None:
    client = await make_client(tmp_path)
    with client:
        headers = register(client, "monitor-user")
        created = client.post(
            "/api/v1/domains",
            json={"name": "example.com", "monitor_enabled": True},
            headers=headers,
        )
        assert created.status_code == 201
        tasks = client.get("/api/v1/tasks", headers=headers).json()
        assert tasks["total"] == 1
        assert tasks["items"][0]["status"] == "queued"

        domain = client.post(
            "/api/v1/domains",
            json={"name": "example.net", "monitor_enabled": False},
            headers=headers,
        ).json()["domain"]
        enabled = client.patch(
            f"/api/v1/domains/{domain['id']}",
            json={"monitor_enabled": True},
            headers={**headers, "If-Match": f'"{domain["version"]}"'},
        )
        assert enabled.status_code == 200
        tasks = client.get("/api/v1/tasks", headers=headers).json()
        assert tasks["total"] == 2


@pytest.mark.asyncio
@pytest.mark.api
async def test_domain_owner_isolation_and_validation(tmp_path: Path) -> None:
    client = await make_client(tmp_path)
    with client:
        first = register(client, "first-user")
        second = register(client, "second-user")
        created = client.post(
            "/api/v1/domains", json={"name": "example.com"}, headers=first
        )
        domain_id = created.json()["domain"]["id"]

        assert (
            client.get(f"/api/v1/domains/{domain_id}", headers=second).status_code
            == 404
        )
        assert (
            client.patch(
                f"/api/v1/domains/{domain_id}",
                json={"notes": "no"},
                headers={**second, "If-Match": '"1"'},
            ).status_code
            == 404
        )
        assert (
            client.delete(f"/api/v1/domains/{domain_id}", headers=second).status_code
            == 404
        )
        duplicate = client.post(
            "/api/v1/domains", json={"name": "example.com"}, headers=first
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "domain_already_managed"
        assert (
            client.post(
                "/api/v1/domains", json={"name": "example.com"}, headers=second
            ).status_code
            == 201
        )

        subdomain = client.post(
            "/api/v1/domains", json={"name": "www.example.com"}, headers=first
        )
        assert subdomain.status_code == 422
        assert subdomain.json()["code"] == "subdomain_not_supported"


def set_registrar_expiry(database: Path, name: str, expires_at: datetime) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE managed_domain SET registrar_expires_at = ? WHERE name_ascii = ?",
            (expires_at.isoformat(), name),
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
@pytest.mark.api
async def test_domain_stats_and_lifecycle_filters_keep_expired_separate(
    tmp_path: Path,
) -> None:
    database = tmp_path / "domains-api.db"
    await run_migrations(sqlite_database(database))
    settings = Settings(
        _env_file=None,
        database_type="sqlite",
        database_path=str(database),
        jwt_secret_key="x",
        refresh_token_pepper="y",
        registration_enabled=True,
    )
    client = TestClient(create_app(settings))
    now = datetime.now(UTC)
    with client:
        headers = register(client, "stats-user")
        monitored = client.post(
            "/api/v1/domains",
            json={"name": "live.com", "monitor_enabled": True},
            headers=headers,
        )
        unmonitored = client.post(
            "/api/v1/domains",
            json={"name": "quiet.com", "monitor_enabled": False},
            headers=headers,
        )
        expiring = client.post(
            "/api/v1/domains",
            json={"name": "soon.com", "monitor_enabled": True},
            headers=headers,
        )
        expired = client.post(
            "/api/v1/domains",
            json={"name": "old.com", "monitor_enabled": True},
            headers=headers,
        )
        assert {item.status_code for item in (monitored, unmonitored, expiring, expired)} == {201}

        set_registrar_expiry(database, "soon.com", now + timedelta(days=7))
        set_registrar_expiry(database, "old.com", now - timedelta(days=25))
        set_registrar_expiry(database, "live.com", now + timedelta(days=400))

        settings_response = client.patch(
            "/api/v1/auth/me/settings",
            json={"expiration_warning_days": [30, 7]},
            headers=headers,
        )
        assert settings_response.status_code == 200

        stats = client.get("/api/v1/domains/stats", headers=headers)
        assert stats.status_code == 200
        assert stats.json() == {
            "managed": 4,
            "monitored": 3,
            "expiring": 1,
            "expired": 1,
            "warning_days": 30,
        }

        expiring_list = client.get(
            "/api/v1/domains",
            params={"lifecycle": "expiring"},
            headers=headers,
        )
        expired_list = client.get(
            "/api/v1/domains",
            params={"lifecycle": "expired"},
            headers=headers,
        )
        monitored_list = client.get(
            "/api/v1/domains",
            params={"monitor_enabled": True},
            headers=headers,
        )
        assert [item["identity"]["ascii_name"] for item in expiring_list.json()["items"]] == [
            "soon.com"
        ]
        assert [item["identity"]["ascii_name"] for item in expired_list.json()["items"]] == [
            "old.com"
        ]
        assert monitored_list.json()["total"] == 3
        rejected = client.get(
            "/api/v1/domains",
            params={"lifecycle": "expired", "expires_from": now.isoformat()},
            headers=headers,
        )
        assert rejected.status_code == 422
