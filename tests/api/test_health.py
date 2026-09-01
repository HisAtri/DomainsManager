import asyncio
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import run_migrations
from tests.database import sqlite_database


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    database = tmp_path / "api.db"
    asyncio.run(run_migrations(sqlite_database(database)))
    settings = Settings(
        database_type="sqlite",
        database_path=str(database),
        jwt_secret_key="x",
        refresh_token_pepper="y",
        migrate_on_startup=True,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.mark.api
def test_live_returns_request_id(client: TestClient) -> None:
    response = client.get(
        "/health/live",
        headers={"X-Request-ID": "request-1234"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "request-1234"


def test_access_log_is_structured_and_excludes_request_secrets(
    client: TestClient,
    caplog,
) -> None:
    with caplog.at_level(logging.INFO, logger="domainsmanager.access"):
        response = client.get(
            "/health/live?token=query-secret",
            headers={
                "Authorization": "Bearer header-secret",
                "Cookie": "session=cookie-secret",
                "X-Request-ID": "request-access-log",
            },
        )

    record = next(
        item for item in caplog.records if item.name == "domainsmanager.access"
    )
    event = json.loads(record.message)
    assert event == {
        "event": "http_request",
        "request_id": "request-access-log",
        "method": "GET",
        "path": "/health/live",
        "status_code": response.status_code,
        "duration_ms": event["duration_ms"],
    }
    assert event["duration_ms"] >= 0
    assert "query-secret" not in record.message
    assert "header-secret" not in record.message
    assert "cookie-secret" not in record.message


@pytest.mark.api
def test_live_replaces_invalid_request_id(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "bad"})

    assert response.status_code == 200
    assert len(response.headers["X-Request-ID"]) == 32


@pytest.mark.api
def test_missing_and_unsupported_routes_return_uniform_errors(client: TestClient) -> None:
    missing = client.get("/not-a-route", headers={"X-Request-ID": "request-4040"})
    assert missing.status_code == 503
    assert missing.json() == {
        "code": "frontend_unavailable",
        "message": "Bundled frontend assets are unavailable; install a release wheel built with the frontend assets.",
        "request_id": "request-4040",
    }

    method_not_allowed = client.post(
        "/health/live", headers={"X-Request-ID": "request-4050"}
    )
    assert method_not_allowed.status_code == 405
    assert method_not_allowed.json() == {
        "code": "http_error",
        "message": "Method Not Allowed",
        "request_id": "request-4050",
    }
    assert method_not_allowed.headers["Allow"] == "GET"


@pytest.mark.api
def test_ready_checks_database(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.api
def test_ready_returns_uniform_error_when_database_is_unavailable() -> None:
    resources = SimpleNamespace(
        database_ready=AsyncMock(return_value=False),
        close=AsyncMock(),
    )
    settings = Settings(
        _env_file=None,
        database_type="sqlite",
        database_path=":memory:",
    )

    with TestClient(
        create_app(settings, resource_factory=lambda _: resources)
    ) as test_client:
        response = test_client.get(
            "/health/ready",
            headers={"X-Request-ID": "request-5678"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "code": "service_unavailable",
        "message": "Database is unavailable",
        "request_id": "request-5678",
    }
    resources.close.assert_awaited_once()


@pytest.mark.api
def test_startup_migrates_an_empty_sqlite_database(tmp_path: Path) -> None:
    database = tmp_path / "fresh.db"
    settings = Settings(
        _env_file=None,
        database_type="sqlite",
        database_path=str(database),
        jwt_secret_key="x",
        refresh_token_pepper="y",
        migrate_on_startup=True,
    )

    with TestClient(create_app(settings)) as test_client:
        response = test_client.get("/health/ready")

    assert response.status_code == 200
