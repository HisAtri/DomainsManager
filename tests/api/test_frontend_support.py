from pathlib import Path

from fastapi.testclient import TestClient

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import run_migrations
from tests.database import sqlite_database


def test_oauth_empty_state_and_cors(tmp_path: Path) -> None:
    database = tmp_path / "oauth-api.db"
    import asyncio

    asyncio.run(run_migrations(sqlite_database(database)))
    settings = Settings(
        _env_file=None,
        database_type="sqlite",
        database_path=str(database),
        jwt_secret_key="x",
        refresh_token_pepper="y",
        cors_origins=["http://localhost:5173"],
    )
    with TestClient(create_app(settings)) as client:
        providers = client.get("/api/v1/auth/oauth2/providers")
        assert providers.status_code == 200
        assert providers.json() == {"items": []}
        unavailable = client.get("/api/v1/auth/oauth2/github/authorize")
        assert unavailable.status_code == 404
        assert unavailable.json()["code"] == "oauth_provider_not_found"
        preflight = client.options(
            "/api/v1/domains",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert preflight.status_code == 200
        assert (
            preflight.headers["access-control-allow-origin"] == "http://localhost:5173"
        )
