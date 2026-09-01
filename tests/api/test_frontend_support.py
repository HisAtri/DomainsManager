from pathlib import Path

from fastapi.testclient import TestClient

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import run_migrations
from tests.database import sqlite_database


def frontend_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "frontend"
    assets = directory / "assets"
    assets.mkdir(parents=True)
    (directory / "index.html").write_text("<div id=\"root\">app</div>", encoding="utf-8")
    (assets / "app-123.js").write_text("console.log('app')", encoding="utf-8")
    (directory / "default.svg").write_text("<svg />", encoding="utf-8")
    return directory


def frontend_settings(database: Path, frontend: Path, **values: object) -> Settings:
    return Settings(
        _env_file=None,
        database_type="sqlite",
        database_path=str(database),
        jwt_secret_key="x",
        refresh_token_pepper="y",
        frontend_dist_path=frontend,
        **values,
    )


def test_oauth_empty_state_and_cors(tmp_path: Path) -> None:
    database = tmp_path / "oauth-api.db"
    import asyncio

    asyncio.run(run_migrations(sqlite_database(database)))
    settings = frontend_settings(
        database,
        frontend_directory(tmp_path),
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


def test_frontend_routes_assets_and_reserved_paths(tmp_path: Path) -> None:
    database = tmp_path / "frontend-api.db"
    import asyncio

    asyncio.run(run_migrations(sqlite_database(database)))
    settings = frontend_settings(database, frontend_directory(tmp_path))
    with TestClient(create_app(settings)) as client:
        for path in ("/", "/email/verify", "/any/client/route"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.text == '<div id="root">app</div>'
            assert response.headers["cache-control"] == "no-cache"

        asset = client.get("/assets/app-123.js")
        assert asset.status_code == 200
        assert asset.text == "console.log('app')"
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert asset.headers["content-type"].startswith("text/javascript")
        assert client.head("/assets/app-123.js").status_code == 200
        assert client.get("/default.svg").status_code == 200

        missing_asset = client.get("/assets/missing.js")
        assert missing_asset.status_code == 404
        assert missing_asset.json()["code"] == "http_error"
        for path in ("/api/v1/missing", "/health/missing"):
            response = client.get(path)
            assert response.status_code == 404
            assert response.json()["code"] == "http_error"


def test_docs_are_disabled_by_default_and_can_be_enabled(tmp_path: Path) -> None:
    database = tmp_path / "docs-api.db"
    import asyncio

    asyncio.run(run_migrations(sqlite_database(database)))
    frontend = frontend_directory(tmp_path)
    with TestClient(create_app(frontend_settings(database, frontend))) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404

    with TestClient(
        create_app(frontend_settings(database, frontend, docs_enabled=True))
    ) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/redoc").status_code == 200
        assert client.get("/openapi.json").status_code == 200
