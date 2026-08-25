import pytest
from fastapi.testclient import TestClient

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import (
    create_engine,
    create_session_factory,
    run_migrations,
)
from domainsmanager_persistence.models import GlobalSetting
from tests.postgres import clean_project_schema, postgres_database


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.api
@pytest.mark.integration
async def test_fastapi_authentication_flow_against_postgresql() -> None:
    database = postgres_database()
    await clean_project_schema(database)
    await run_migrations(database)
    values = database.model_dump()
    settings = Settings(
        _env_file=None,
        database_type=values["type"],
        database_host=values["host"],
        database_port=values["port"],
        database_name=values["name"],
        database_user=values["user"],
        database_password=values["password"],
        database_ssl_mode=values["ssl_mode"],
        database_pool_size=2,
        database_max_overflow=0,
        jwt_secret_key="x",
        refresh_token_pepper="y",
        registration_enabled=True,
        bootstrap_admin_username="postgres-admin",
        bootstrap_admin_password="123456",
        configuration_encryption_key="eAbLHc58_pjXLGKKZNoeuQLHYKkN9orkVRxMVokhGTY=",
    )
    try:
        with TestClient(create_app(settings)) as client:
            registered = client.post(
                "/api/v1/auth/register",
                json={"username": "postgres-user", "password": "123456"},
            )
            assert registered.status_code == 201
            tokens = registered.json()["tokens"]

            me = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            assert me.status_code == 200
            assert me.json()["username"] == "postgres-user"

            rotated = client.post(
                "/api/v1/auth/token/refresh",
                json={"refresh_token": tokens["refresh_token"]},
            )
            assert rotated.status_code == 200

            logout = client.post(
                "/api/v1/auth/logout",
                json={"refresh_token": rotated.json()["refresh_token"]},
            )
            assert logout.status_code == 204

            rejected = client.get(
                "/api/v1/auth/me",
                headers={
                    "Authorization": f"Bearer {rotated.json()['access_token']}"
                },
            )
            assert rejected.status_code == 401
    finally:
        await clean_project_schema(database)


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.api
@pytest.mark.integration
async def test_postgresql_admin_settings_are_versioned_and_secrets_are_encrypted() -> None:
    database = postgres_database()
    await clean_project_schema(database)
    await run_migrations(database)
    values = database.model_dump()
    settings = Settings(
        _env_file=None,
        database_type=values["type"], database_host=values["host"],
        database_port=values["port"], database_name=values["name"],
        database_user=values["user"], database_password=values["password"],
        database_ssl_mode=values["ssl_mode"], jwt_secret_key="x",
        refresh_token_pepper="y", bootstrap_admin_username="postgres-admin",
        bootstrap_admin_password="123456",
        configuration_encryption_key="eAbLHc58_pjXLGKKZNoeuQLHYKkN9orkVRxMVokhGTY=",
    )
    try:
        with TestClient(create_app(settings)) as client:
            login = client.post("/api/v1/auth/login", data={"username": "postgres-admin", "password": "123456"})
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}
            first = client.put("/api/v1/admin/settings/check_interval_seconds", json={"value": 3600}, headers={**headers, "If-Match": "0"})
            assert first.status_code == 200
            assert first.json()["version"] == 1
            assert client.put("/api/v1/admin/settings/check_interval_seconds", json={"value": 7200}, headers={**headers, "If-Match": "0"}).status_code == 409
            secret = client.put("/api/v1/admin/settings/smtp_password", json={"value": "postgres-smtp-password"}, headers={**headers, "If-Match": "0"})
            assert secret.status_code == 200
            assert secret.json()["value"] is None
            assert "postgres-smtp-password" not in client.get("/api/v1/admin/settings", headers=headers).text
        engine = create_engine(database)
        try:
            async with create_session_factory(engine)() as session:
                stored = await session.get(GlobalSetting, "smtp_password")
            assert stored is not None
            assert stored.value.startswith("fernet:v1:")
            assert "postgres-smtp-password" not in stored.value
        finally:
            await engine.dispose()
    finally:
        await clean_project_schema(database)
