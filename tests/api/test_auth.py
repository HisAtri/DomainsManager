from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import run_migrations
from tests.database import sqlite_database


async def make_client(
    tmp_path: Path,
    *,
    registration_enabled: bool = True,
    bootstrap_admin_username: str | None = None,
    bootstrap_admin_password: str | None = None,
):
    database = tmp_path / "auth-api.db"
    await run_migrations(sqlite_database(database))
    settings = Settings(
        _env_file=None,
        database_type="sqlite",
        database_path=str(database),
        jwt_secret_key="x",
        refresh_token_pepper="y",
        registration_enabled=registration_enabled,
        bootstrap_admin_username=bootstrap_admin_username,
        bootstrap_admin_password=bootstrap_admin_password,
    )
    return TestClient(create_app(settings))


@pytest.mark.asyncio
@pytest.mark.api
async def test_register_me_settings_refresh_and_logout(tmp_path: Path) -> None:
    client = await make_client(tmp_path)
    with client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "Test.User",
                "password": "123456",
                "email": "user@example.com",
            },
            headers={"X-Request-ID": "request-1234"},
        )
        assert registered.status_code == 201
        assert registered.headers["Cache-Control"] == "no-store"
        assert registered.headers["Pragma"] == "no-cache"
        assert registered.headers["Location"].endswith("/api/v1/auth/me")
        assert registered.headers["X-Request-ID"] == "request-1234"
        payload = registered.json()
        access_token = payload["tokens"]["access_token"]
        refresh_token = payload["tokens"]["refresh_token"]

        me = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert me.status_code == 200
        assert me.json()["username"] == "Test.User"
        assert me.json()["role"] == "user"

        settings = client.patch(
            "/api/v1/auth/me/settings",
            json={
                "timezone": "Asia/Shanghai",
                "expiration_warning_days": [30, 7, 1],
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert settings.status_code == 200
        assert settings.json() == {
            "locale": "zh-CN",
            "timezone": "Asia/Shanghai",
            "default_monitor_enabled": True,
            "expiration_warning_days": [30, 7, 1],
        }

        rotated = client.post(
            "/api/v1/auth/token/refresh",
            json={"refresh_token": refresh_token},
        )
        assert rotated.status_code == 200
        rotated_payload = rotated.json()
        rotated_access = rotated_payload["access_token"]
        rotated_refresh = rotated_payload["refresh_token"]

        replay = client.post(
            "/api/v1/auth/token/refresh",
            json={"refresh_token": refresh_token},
        )
        assert replay.status_code == 401
        assert replay.json()["code"] == "refresh_token_replayed"
        assert replay.headers["WWW-Authenticate"] == "Bearer"

        revoked = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {rotated_access}"},
        )
        assert revoked.status_code == 401

        logout = client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": rotated_refresh},
        )
        assert logout.status_code == 204


@pytest.mark.asyncio
@pytest.mark.api
async def test_login_change_password_and_profile_patch(tmp_path: Path) -> None:
    client = await make_client(tmp_path)
    with client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"username": "test-user", "password": "123456"},
        ).json()
        access_token = registered["tokens"]["access_token"]

        login = client.post(
            "/api/v1/auth/login",
            data={"username": "test-user", "password": "123456"},
        )
        assert login.status_code == 200
        assert login.headers["Cache-Control"] == "no-store"

        profile = client.patch(
            "/api/v1/auth/me",
            json={"email": "new@example.com"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert profile.status_code == 200
        assert profile.json()["email"] == "new@example.com"

        changed = client.post(
            "/api/v1/auth/me/password",
            json={"current_password": "123456", "new_password": "654321"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert changed.status_code == 204

        old_login = client.post(
            "/api/v1/auth/login",
            data={"username": "test-user", "password": "123456"},
        )
        assert old_login.status_code == 401
        assert old_login.json()["code"] == "invalid_credentials"
        new_login = client.post(
            "/api/v1/auth/login",
            data={"username": "test-user", "password": "654321"},
        )
        assert new_login.status_code == 200


@pytest.mark.asyncio
@pytest.mark.api
async def test_registration_disabled_and_validation_errors(tmp_path: Path) -> None:
    client = await make_client(tmp_path, registration_enabled=False)
    with client:
        disabled = client.post(
            "/api/v1/auth/register",
            json={"username": "test-user", "password": "123456"},
        )
        assert disabled.status_code == 403
        assert disabled.json()["code"] == "registration_disabled"

        too_short = client.post(
            "/api/v1/auth/register",
            json={"username": "test-user", "password": "12345"},
        )
        assert too_short.status_code == 422

        missing_token = client.get("/api/v1/auth/me")
        assert missing_token.status_code == 401
        assert missing_token.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.asyncio
@pytest.mark.api
async def test_invalid_settings_are_rejected_before_persistence(tmp_path: Path) -> None:
    client = await make_client(tmp_path)
    with client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"username": "test-user", "password": "123456"},
        ).json()
        headers = {
            "Authorization": f"Bearer {registered['tokens']['access_token']}"
        }

        for payload in (
            {"timezone": None},
            {"timezone": "Not/A-Timezone"},
            {"expiration_warning_days": [7, 7]},
        ):
            response = client.patch(
                "/api/v1/auth/me/settings",
                json=payload,
                headers=headers,
            )
            assert response.status_code == 422

        settings = client.get("/api/v1/auth/me/settings", headers=headers)
        assert settings.status_code == 200
        assert settings.json() == {
            "locale": "zh-CN",
            "timezone": "UTC",
            "default_monitor_enabled": True,
            "expiration_warning_days": [],
        }


@pytest.mark.asyncio
@pytest.mark.api
async def test_bootstrap_admin_is_only_applied_to_empty_database(tmp_path: Path) -> None:
    client = await make_client(
        tmp_path,
        registration_enabled=False,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="123456",
    )
    with client:
        login = client.post(
            "/api/v1/auth/login",
            data={"username": "admin", "password": "123456"},
        )
        assert login.status_code == 200
        assert login.json()["user"]["role"] == "admin"

    second = await make_client(
        tmp_path,
        registration_enabled=False,
        bootstrap_admin_username="different-admin",
        bootstrap_admin_password="654321",
    )
    with second:
        original = second.post(
            "/api/v1/auth/login",
            data={"username": "admin", "password": "123456"},
        )
        different = second.post(
            "/api/v1/auth/login",
            data={"username": "different-admin", "password": "654321"},
        )
        assert original.status_code == 200
        assert different.status_code == 401
