import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import run_migrations
from tests.database import sqlite_database


def make_client(tmp_path: Path) -> TestClient:
    database = tmp_path / "site-config.db"
    asyncio.run(run_migrations(sqlite_database(database)))
    return TestClient(
        create_app(
            Settings(
                _env_file=None,
                database_type="sqlite",
                database_path=str(database),
                jwt_secret_key="x",
                refresh_token_pepper="y",
                bootstrap_admin_username="admin",
                bootstrap_admin_password="123456",
            )
        )
    )


@pytest.mark.api
def test_public_site_config_uses_defaults_and_etag(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/api/v1/site/config")
        assert response.status_code == 200
        assert response.json()["site_name"] == "DomainsManager"
        assert response.json()["registration_enabled"] is False
        assert response.json()["smtp_enabled"] is True
        assert response.json()["site_logo"] == "/default.svg"
        assert response.json()["footer_links"] == []
        assert response.headers["etag"]

        not_modified = client.get(
            "/api/v1/site/config", headers={"If-None-Match": response.headers["etag"]}
        )
        assert not_modified.status_code == 304


@pytest.mark.api
def test_admin_updates_site_settings_and_public_config(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        login = client.post("/api/v1/auth/login", data={"username": "admin", "password": "123456"})
        headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}
        current = client.get("/api/v1/admin/settings", headers=headers)
        versions = {item["key"]: item["version"] for item in current.json()}
        updated = client.put(
            "/api/v1/admin/settings",
            headers=headers,
            json={
                "settings": [
                    {"key": "site_name", "value": "我的域名", "version": versions["site_name"]},
                    {
                        "key": "footer_links",
                        "value": [{"label": "项目主页", "url": "https://example.com"}],
                        "version": versions["footer_links"],
                    },
                    {"key": "custom_css", "value": ".brand { color: red; }", "version": versions["custom_css"]},
                ]
            },
        )
        assert updated.status_code == 200
        by_key = {item["key"]: item for item in updated.json()}
        assert by_key["footer_links"]["editor"] == "links"
        assert by_key["custom_css"]["language"] == "css"

        public = client.get("/api/v1/site/config")
        assert public.json()["site_name"] == "我的域名"
        assert public.json()["registration_enabled"] is False
        assert public.json()["footer_links"] == [{"label": "项目主页", "url": "https://example.com"}]
        assert public.json()["custom_css"] == ".brand { color: red; }"

        versions = {item["key"]: item["version"] for item in updated.json()}
        enabled = client.put(
            "/api/v1/admin/settings",
            headers=headers,
            json={
                "settings": [
                    {
                        "key": "registration_enabled",
                        "value": True,
                        "version": versions["registration_enabled"],
                    },
                    {
                        "key": "smtp_enabled",
                        "value": False,
                        "version": versions["smtp_enabled"],
                    },
                ]
            },
        )
        assert enabled.status_code == 200
        assert client.get("/api/v1/site/config").json()["registration_enabled"] is True
        assert client.get("/api/v1/site/config").json()["smtp_enabled"] is False


@pytest.mark.api
def test_footer_links_reject_unsafe_urls(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        login = client.post("/api/v1/auth/login", data={"username": "admin", "password": "123456"})
        headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}
        settings = client.get("/api/v1/admin/settings", headers=headers).json()
        version = next(item["version"] for item in settings if item["key"] == "footer_links")
        response = client.put(
            "/api/v1/admin/settings/footer_links",
            headers={**headers, "If-Match": str(version)},
            json={"value": [{"label": "bad", "url": "javascript:alert(1)"}]},
        )
        assert response.status_code == 422
