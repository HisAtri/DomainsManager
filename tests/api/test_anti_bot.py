from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from altcha import Challenge, Payload, create_challenge, solve_challenge
from fastapi.testclient import TestClient

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import run_migrations
from tests.database import sqlite_database

SECRET = "x"


async def make_client(tmp_path: Path) -> TestClient:
    database = tmp_path / "anti-bot-api.db"
    await run_migrations(sqlite_database(database))
    return TestClient(
        create_app(
            Settings(
                _env_file=None,
                database_type="sqlite",
                database_path=str(database),
                jwt_secret_key=SECRET,
                refresh_token_pepper="y",
                registration_enabled=True,
                bootstrap_admin_username="pow-admin",
                bootstrap_admin_password="123456",
            )
        )
    )


def user_headers(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "123456"},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


def admin_headers(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/login",
        data={"username": "pow-admin", "password": "123456"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}


def enable_pow(client: TestClient) -> None:
    headers = admin_headers(client)
    settings = client.get("/api/v1/admin/settings", headers=headers).json()
    versions = {item["key"]: item["version"] for item in settings}
    response = client.put(
        "/api/v1/admin/settings",
        headers=headers,
        json={
            "settings": [
                {
                    "key": "anti_bot_mode",
                    "value": "image_captcha",
                    "version": versions["anti_bot_mode"],
                },
                {
                    "key": "pow_difficulty",
                    "value": "easy",
                    "version": versions["pow_difficulty"],
                },
            ]
        },
    )
    assert response.status_code == 200


def solve_pow(client: TestClient, operation: str) -> str:
    response = client.get(f"/api/v1/anti-bot/pow?operation={operation}")
    assert response.status_code == 200
    challenge = Challenge.from_dict(response.json())
    solution = solve_challenge(challenge)
    assert solution is not None
    return Payload(challenge, solution).to_base64()


@pytest.mark.asyncio
@pytest.mark.api
async def test_pow_endpoint_requires_image_captcha_mode(tmp_path: Path) -> None:
    client = await make_client(tmp_path)
    with client:
        assert client.get("/api/v1/anti-bot/pow?operation=create_domain").status_code == 404
        enable_pow(client)
        challenge = client.get("/api/v1/anti-bot/pow?operation=create_domain")
        assert challenge.status_code == 200
        assert challenge.json()["parameters"]["data"]["operation"] == "create_domain"


@pytest.mark.asyncio
@pytest.mark.api
async def test_create_domain_accepts_matching_pow_payload(tmp_path: Path) -> None:
    client = await make_client(tmp_path)
    with client:
        headers = user_headers(client, "pow-owner")
        enable_pow(client)
        missing = client.post(
            "/api/v1/domains",
            json={"name": "example.com", "monitor_enabled": False},
            headers=headers,
        )
        assert missing.status_code == 422
        assert missing.json()["code"] == "anti_bot_verification_failed"

        garbage = client.post(
            "/api/v1/domains",
            json={"name": "example.com", "monitor_enabled": False, "pow_payload": "not-a-payload"},
            headers=headers,
        )
        assert garbage.status_code == 422
        assert garbage.json()["code"] == "anti_bot_verification_failed"

        created = client.post(
            "/api/v1/domains",
            json={
                "name": "example.com",
                "monitor_enabled": False,
                "pow_payload": solve_pow(client, "create_domain"),
            },
            headers=headers,
        )
        assert created.status_code == 201


@pytest.mark.asyncio
@pytest.mark.api
async def test_refresh_domain_rejects_create_domain_payload(tmp_path: Path) -> None:
    client = await make_client(tmp_path)
    with client:
        headers = user_headers(client, "pow-refresh")
        created = client.post(
            "/api/v1/domains",
            json={"name": "example.net", "monitor_enabled": False},
            headers=headers,
        )
        assert created.status_code == 201
        domain_id = created.json()["domain"]["id"]
        enable_pow(client)

        mismatched = client.post(
            f"/api/v1/domains/{domain_id}/refresh",
            json={"force_refresh": False, "pow_payload": solve_pow(client, "create_domain")},
            headers={**headers, "Idempotency-Key": "refresh-mismatch-1"},
        )
        assert mismatched.status_code == 422
        assert mismatched.json()["code"] == "anti_bot_verification_failed"

        refreshed = client.post(
            f"/api/v1/domains/{domain_id}/refresh",
            json={"force_refresh": False, "pow_payload": solve_pow(client, "refresh_domain")},
            headers={**headers, "Idempotency-Key": "refresh-ok-1"},
        )
        assert refreshed.status_code == 202


@pytest.mark.asyncio
@pytest.mark.api
async def test_expired_pow_payload_is_rejected(tmp_path: Path) -> None:
    client = await make_client(tmp_path)
    with client:
        headers = user_headers(client, "pow-expired")
        enable_pow(client)
        challenge = create_challenge(
            algorithm="PBKDF2/SHA-256",
            cost=1000,
            hmac_secret=SECRET,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
            data={"operation": "create_domain"},
        )
        solution = solve_challenge(challenge)
        assert solution is not None
        expired = client.post(
            "/api/v1/domains",
            json={
                "name": "expired.example",
                "monitor_enabled": False,
                "pow_payload": Payload(challenge, solution).to_base64(),
            },
            headers=headers,
        )
        assert expired.status_code == 422
        assert expired.json()["code"] == "anti_bot_verification_failed"
