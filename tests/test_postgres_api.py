import pytest
from fastapi.testclient import TestClient

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import run_migrations
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
