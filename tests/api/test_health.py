from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        database_type="sqlite",
        database_path=str(tmp_path / "api.db"),
        jwt_secret_key="x",
        refresh_token_pepper="y",
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


@pytest.mark.api
def test_live_replaces_invalid_request_id(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "bad"})

    assert response.status_code == 200
    assert len(response.headers["X-Request-ID"]) == 32


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
    settings = Settings(database_type="sqlite", database_path=":memory:")

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
