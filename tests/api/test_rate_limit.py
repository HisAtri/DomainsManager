from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domainsmanager_api.main import create_app
from domainsmanager_api.rate_limit import (
    MemoryRateLimitStore,
    RateLimitConfigurationError,
)
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import run_migrations
from tests.database import sqlite_database


async def make_client(tmp_path: Path, **overrides: object) -> TestClient:
    database = tmp_path / "rate-limit-api.db"
    await run_migrations(sqlite_database(database))
    settings = Settings(
        _env_file=None,
        database_type="sqlite",
        database_path=str(database),
        jwt_secret_key="x",
        refresh_token_pepper="y",
        registration_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="123456",
        **overrides,
    )
    return TestClient(create_app(settings))


def register(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/register", json={"username": username, "password": "123456"}
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", data={"username": username, "password": "123456"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


@pytest.mark.asyncio
@pytest.mark.api
async def test_memory_store_isolates_policies_and_subjects() -> None:
    store = MemoryRateLimitStore()
    assert (await store.consume("first", "normal", "one", 1, 60))[0]
    assert not (await store.consume("first", "normal", "one", 1, 60))[0]
    assert (await store.consume("second", "normal", "one", 1, 60))[0]
    assert (await store.consume("first", "expensive", "one", 1, 60))[0]


@pytest.mark.asyncio
@pytest.mark.api
async def test_authenticated_requests_use_normal_and_expensive_policies(
    tmp_path: Path,
) -> None:
    client = await make_client(
        tmp_path,
        normal_rate_limit_attempts=2,
        expensive_rate_limit_attempts=2,
    )
    with client:
        first = register(client, "first-rate-user")
        second = register(client, "second-rate-user")

        assert client.get("/api/v1/domains", headers=first).status_code == 200
        assert client.get("/api/v1/domains", headers=first).status_code == 200
        limited = client.get(
            "/api/v1/domains", headers={**first, "X-Request-ID": "normal-limit-01"}
        )
        assert limited.status_code == 429
        assert limited.json() == {
            "code": "rate_limited",
            "message": "Too many requests",
            "request_id": "normal-limit-01",
        }
        assert limited.headers["Cache-Control"] == "no-store"
        assert limited.headers["Retry-After"]
        assert client.get("/api/v1/domains", headers=second).status_code == 200

        created = client.post(
            "/api/v1/domains",
            json={"name": "example.com", "monitor_enabled": False},
            headers=first,
        )
        assert created.status_code == 201
        domain_id = created.json()["domain"]["id"]
        refreshed = client.post(
            f"/api/v1/domains/{domain_id}/refresh",
            json={"force_refresh": False},
            headers={**first, "Idempotency-Key": "manual-refresh-001"},
        )
        assert refreshed.status_code == 202
        expensive_limited = client.post(
            "/api/v1/domains",
            json={"name": "example.org", "monitor_enabled": False},
            headers=first,
        )
        assert expensive_limited.status_code == 429


@pytest.mark.asyncio
@pytest.mark.api
async def test_administrator_can_update_rate_limit_policy(tmp_path: Path) -> None:
    client = await make_client(tmp_path, normal_rate_limit_attempts=3)
    with client:
        admin = login(client, "admin")
        settings = client.get("/api/v1/admin/settings", headers=admin)
        assert settings.status_code == 200
        normal = next(
            item
            for item in settings.json()
            if item["key"] == "normal_rate_limit_attempts"
        )
        updated = client.put(
            "/api/v1/admin/settings/normal_rate_limit_attempts",
            json={"value": 1},
            headers={**admin, "If-Match": str(normal["version"])},
        )
        assert updated.status_code == 200
        assert client.get("/api/v1/domains", headers=admin).status_code == 200
        assert client.get("/api/v1/domains", headers=admin).status_code == 429


@pytest.mark.asyncio
@pytest.mark.api
async def test_redis_backend_requires_its_url_at_startup(tmp_path: Path) -> None:
    client = await make_client(tmp_path, rate_limit_backend="redis")
    with pytest.raises(RateLimitConfigurationError, match="REDIS_URL"), client:
        pass
