import asyncio
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings
from domainsmanager_application.oauth import ExternalIdentity
from domainsmanager_persistence.db import run_migrations
from domainsmanager_persistence.models import (
    AppUser,
    OAuthAuthorizationAttempt,
    UserAuthIdentity,
)
from tests.database import sqlite_database

pytestmark = [pytest.mark.api]


def test_uvicorn_access_log_redacts_oauth_query():
    import logging

    from domainsmanager_api.middleware import OAuthAccessLogFilter

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        (
            "localhost",
            "GET",
            "/api/v1/auth/oauth2/github/callback?code=secret&state=secret",
            "1.1",
            303,
        ),
        None,
    )
    assert OAuthAccessLogFilter().filter(record)
    assert "secret" not in record.getMessage()
    assert "callback" in record.getMessage()


class FakeProvider:
    key = "github"
    display_name = "GitHub"
    pkce = True

    def __init__(self, key="github", subject="123"):
        self.key, self.subject = key, subject
        self.calls = 0
        self.active = True

    def authorization_url(self, state, redirect_uri, code_challenge):
        assert redirect_uri.endswith(f"/{self.key}/callback")
        assert code_challenge
        return f"https://provider.example/authorize?state={state}"

    async def fetch_identity(self, code, redirect_uri, code_verifier):
        self.calls += 1
        assert code == "test-code"
        assert len(code_verifier) == 43
        return ExternalIdentity(
            self.key,
            self.subject,
            "display-user",
            "Name",
            None,
            self.active,
            {"id": self.subject},
        )


@pytest.fixture
def client(tmp_path):
    database = tmp_path / "oauth.db"
    asyncio.run(run_migrations(sqlite_database(database)))
    settings = Settings(
        _env_file=None,
        database_path=str(database),
        jwt_secret_key="x",
        refresh_token_pepper="y",
        registration_enabled=True,
    )
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        client.app.state.resources.oauth.configuration_loader = None
        client.app.state.resources.oauth.base_url = "http://localhost"
        client.app.state.resources.oauth.providers = {
            "github": FakeProvider(),
            "linuxdo": FakeProvider("linuxdo"),
        }
        yield client


def begin(client, provider="github", headers=None):
    endpoint = f"/api/v1/auth/oauth2/{provider}/authorize"
    response = (
        client.post(endpoint, headers=headers)
        if headers
        else client.get(endpoint, follow_redirects=False)
    )
    assert response.status_code == (200 if headers else 302)
    url = (
        response.json()["authorization_url"]
        if headers
        else response.headers["location"]
    )
    return parse_qs(urlsplit(url).query)["state"][0]


def test_database_settings_enable_only_fixed_provider_routes(tmp_path):
    database = tmp_path / "configured-oauth.db"
    asyncio.run(run_migrations(sqlite_database(database)))
    values = {
        "site_url": "http://localhost:5173",
        "github_client_id": "github-test-id",
        "github_client_secret": "github-test-secret",
    }
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO global_setting (key, value, version, updated_at) VALUES (?, ?, 1, ?)",
            [
                (key, value, datetime.now(UTC).isoformat())
                for key, value in values.items()
            ],
        )
    settings = Settings(
        _env_file=None,
        database_path=str(database),
        jwt_secret_key="test",
        refresh_token_pepper="test",
    )
    with TestClient(create_app(settings), base_url="http://localhost:5173") as client:
        assert client.get("/api/v1/auth/oauth2/availability").json() == {
            "github": True,
            "linuxdo": False,
        }
        response = client.get(
            "/api/v1/auth/oauth2/github/authorize", follow_redirects=False
        )
        assert response.status_code == 302
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query["redirect_uri"] == [
            "http://localhost:5173/api/v1/auth/oauth2/github/callback"
        ]
        assert client.get("/api/v1/auth/oauth2/linuxdo/authorize").status_code == 404
        assert client.get("/api/v1/auth/oauth2/other/authorize").status_code == 404
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO global_setting (key, value, version, updated_at) VALUES ('github_enabled', 'false', 1, ?)",
                (datetime.now(UTC).isoformat(),),
            )
        assert client.get("/api/v1/auth/oauth2/availability").json()["github"] is False
        assert client.get("/api/v1/auth/oauth2/github/authorize").status_code == 404
        interrupted = complete(client, query["state"][0])
        assert interrupted.status_code == 303
        assert interrupted.headers["location"].endswith(
            "#dashboard?oauth_error=oauth_provider_not_found"
        )
        with sqlite3.connect(database) as connection:
            assert (
                connection.execute(
                    "SELECT value FROM global_setting WHERE key = 'github_client_secret'"
                ).fetchone()[0]
                == "github-test-secret"
            )
            connection.execute(
                "UPDATE global_setting SET value = 'true' WHERE key = 'github_enabled'"
            )
        assert client.get("/api/v1/auth/oauth2/availability").json()["github"] is True
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE global_setting SET value = '' WHERE key = 'github_client_secret'"
            )
        assert client.get("/api/v1/auth/oauth2/availability").json()["github"] is False
        assert client.get("/api/v1/auth/oauth2/github/authorize").status_code == 404


def complete(client, state, provider="github", **params):
    return client.get(
        f"/api/v1/auth/oauth2/{provider}/callback",
        params={"state": state, "code": "test-code", **params},
        follow_redirects=False,
    )


def login(client, provider="github"):
    response = complete(client, begin(client, provider), provider)
    assert response.status_code == 303
    token = client.post("/api/v1/auth/token/refresh").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    user = client.get("/api/v1/auth/me", headers=headers).json()
    target = "onboarding" if user["username_setup_required"] else "dashboard"
    assert response.headers["location"].endswith(f"#{target}?oauth=success")
    return headers, user


@pytest.mark.parametrize("provider", ["github", "linuxdo"])
def test_initial_username_is_persisted_and_can_only_be_set_once(client, provider):
    headers, user = login(client, provider)
    assert user["username_setup_required"] is True
    _, pending = login(client, provider)
    assert pending["username_setup_required"] is True
    for invalid in ["ab", "bad name", "中文", "x" * 129]:
        assert (
            client.post(
                "/api/v1/auth/me/username", headers=headers, json={"username": invalid}
            ).status_code
            == 422
        )
    response = client.post(
        "/api/v1/auth/me/username", headers=headers, json={"username": "My.Name-123"}
    )
    assert response.status_code == 200
    assert response.json()["username"] == "My.Name-123"
    assert response.json()["username_setup_required"] is False
    again = client.post(
        "/api/v1/auth/me/username", headers=headers, json={"username": "changed"}
    )
    assert again.status_code == 409
    assert again.json()["code"] == "username_already_set"
    _, returning = login(client, provider)
    assert returning["id"] == user["id"]
    assert returning["username"] == "My.Name-123"
    assert returning["username_setup_required"] is False


def test_username_conflict_does_not_consume_setup_and_local_accounts_cannot_rename(
    client,
):
    local = client.post(
        "/api/v1/auth/register", json={"username": "Existing", "password": "1234567"}
    ).json()
    local_headers = {"Authorization": f"Bearer {local['tokens']['access_token']}"}
    assert (
        client.post(
            "/api/v1/auth/me/username",
            headers=local_headers,
            json={"username": "renamed"},
        ).status_code
        == 409
    )
    headers, _ = login(client)
    conflict = client.post(
        "/api/v1/auth/me/username", headers=headers, json={"username": "EXISTING"}
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "username_taken"
    assert (
        client.get("/api/v1/auth/me", headers=headers).json()["username_setup_required"]
        is True
    )
    assert (
        client.post(
            "/api/v1/auth/me/username", headers=headers, json={"username": "Available"}
        ).status_code
        == 200
    )


def test_concurrent_username_setup_only_accepts_one_submission(client):
    headers, _ = login(client)
    oauth = client.app.state.resources.oauth

    async def race():
        from domainsmanager_application.oauth import OAuthError
        from domainsmanager_application.services import AuthContext

        current = await oauth.auth.authenticate_access_token(
            headers["Authorization"].split()[1]
        )
        results = await asyncio.gather(
            oauth.set_username(current, "first-choice", AuthContext()),
            oauth.set_username(current, "second-choice", AuthContext()),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert any(
            isinstance(result, OAuthError) and result.code == "username_already_set"
            for result in results
        )

    client.portal.call(race)


def test_login_cookie_replay_and_password_setup(client):
    state = begin(client)
    callback = complete(client, state)
    assert callback.headers["cache-control"] == "no-store"
    assert "HttpOnly" in callback.headers["set-cookie"]
    assert "test-code" not in callback.headers["location"]
    assert "oauth_invalid_state" in complete(client, state).headers["location"]
    token = client.post("/api/v1/auth/token/refresh").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    user = client.get("/api/v1/auth/me", headers=headers).json()
    assert not user["password_auth_enabled"]
    assert user["username"] == "github-123"
    assert (
        client.delete("/api/v1/auth/oauth2/github", headers=headers).json()["code"]
        == "oauth_last_login_method"
    )
    assert (
        client.post(
            "/api/v1/auth/me/password/set",
            headers=headers,
            json={"new_password": "1234567"},
        ).status_code
        == 204
    )
    token = client.post("/api/v1/auth/token/refresh").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert (
        client.post(
            "/api/v1/auth/me/password/set",
            headers=headers,
            json={"new_password": "7654321"},
        ).status_code
        == 409
    )
    assert (
        client.delete("/api/v1/auth/oauth2/github", headers=headers).status_code == 204
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            data={"username": user["username"], "password": "1234567"},
        ).status_code
        == 200
    )


def test_browser_and_provider_binding(client):
    state = begin(client)
    cookies = list(client.cookies.jar)
    client.cookies.clear()
    assert "oauth_invalid_state" in complete(client, state).headers["location"]
    for cookie in cookies:
        client.cookies.jar.set_cookie(cookie)
    assert (
        "oauth_invalid_state" in complete(client, state, "linuxdo").headers["location"]
    )
    # 错误的提供商不会消费请求，但会删除浏览器该次 cookie；恢复后原提供商仍可成功。
    for cookie in cookies:
        client.cookies.jar.set_cookie(cookie)
    assert "oauth=success" in complete(client, state).headers["location"]


def test_registration_switch_existing_login_and_link(client):
    headers, user = login(client)
    oauth = client.app.state.resources.oauth
    oauth.auth._configuration = replace(
        oauth.auth._configuration, registration_enabled=False
    )
    assert (
        "oauth=linked"
        in complete(client, begin(client, "linuxdo", headers), "linuxdo").headers[
            "location"
        ]
    )
    _, again = login(client, "linuxdo")
    assert again["id"] == user["id"]
    oauth.providers["github"].subject = "456"
    response = complete(client, begin(client))
    assert "registration_disabled" in response.headers["location"]


def test_identity_cannot_be_stolen_or_replaced(client):
    first_headers, first = login(client)
    provider = client.app.state.resources.oauth.providers["github"]
    provider.subject = "456"
    second_headers, second = login(client)
    assert second["id"] != first["id"]
    provider.subject = "123"
    assert (
        "oauth_identity_in_use"
        in complete(client, begin(client, headers=second_headers)).headers["location"]
    )
    provider.subject = "789"
    assert (
        "oauth_provider_already_linked"
        in complete(client, begin(client, headers=second_headers)).headers["location"]
    )
    assert (
        client.get("/api/v1/auth/oauth2/accounts", headers=first_headers).status_code
        == 200
    )


def test_logout_invalidates_link_and_cancel_is_one_time(client):
    headers, _ = login(client)
    state = begin(client, "linuxdo", headers)
    client.post("/api/v1/auth/logout")
    assert "invalid_token" in complete(client, state, "linuxdo").headers["location"]
    assert client.app.state.resources.oauth.providers["linuxdo"].calls == 0
    state = begin(client)
    assert (
        "oauth_access_denied"
        in complete(client, state, error="access_denied").headers["location"]
    )
    assert "oauth_invalid_state" in complete(client, state).headers["location"]


def test_expired_requests_banned_and_inactive_users(client):
    headers, _ = login(client)
    oauth = client.app.state.resources.oauth
    real_clock = oauth.clock
    state = begin(client)
    oauth.clock = lambda: real_clock() + timedelta(minutes=11)
    assert "oauth_invalid_state" in complete(client, state).headers["location"]
    assert (
        client.post("/api/v1/auth/oauth2/linuxdo/authorize", headers=headers).json()[
            "code"
        ]
        == "oauth_reauthentication_required"
    )
    oauth.clock = real_clock
    oauth.providers["github"].active = False
    assert (
        "oauth_inactive_identity" in complete(client, begin(client)).headers["location"]
    )
    oauth.providers["github"].active = True

    async def ban():
        async with client.app.state.resources.sessions() as session:
            await session.execute(update(AppUser).values(is_active=False))
            await session.commit()

    client.portal.call(ban)
    assert "account_banned" in complete(client, begin(client)).headers["location"]


def test_duplicate_registration_is_atomic(client):
    oauth = client.app.state.resources.oauth

    async def race():
        starts = [await oauth.begin("github") for _ in range(2)]
        from domainsmanager_application.services import AuthContext

        results = await asyncio.gather(
            *(
                oauth.complete(
                    "github", state, browser, "test-code", None, None, AuthContext()
                )
                for _, state, browser in starts
            )
        )
        assert results[0][0].user.id == results[1][0].user.id
        async with client.app.state.resources.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(AppUser)) == 1
            assert (
                await session.scalar(select(func.count()).select_from(UserAuthIdentity))
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(OAuthAuthorizationAttempt)
                    .where(OAuthAuthorizationAttempt.consumed_at.is_not(None))
                )
                == 2
            )

    client.portal.call(race)


def test_revoked_authenticated_snapshot_cannot_change_login_methods(client):
    headers, _ = login(client)
    oauth = client.app.state.resources.oauth

    async def exercise():
        from domainsmanager_application.oauth import OAuthError
        from domainsmanager_application.services import AuthContext

        current = await oauth.auth.authenticate_access_token(
            headers["Authorization"].split()[1]
        )
        async with oauth.uow() as uow:
            await uow.sessions.revoke_session(current.session.id, oauth.clock(), "test")
            await uow.commit()
        for operation in (
            oauth.set_password(current, "valid-password", AuthContext()),
            oauth.unlink("github", current, AuthContext()),
        ):
            with pytest.raises(OAuthError, match="Session changed"):
                await operation
        async with oauth.uow() as uow:
            assert not (
                await uow.users.get_by_id(current.user.id)
            ).password_auth_enabled
            assert len(await uow.oauth.list_identities(current.user.id)) == 1

    client.portal.call(exercise)


def test_concurrent_unlinks_keep_a_login_method(client):
    headers, _ = login(client)
    assert (
        "oauth=linked"
        in complete(client, begin(client, "linuxdo", headers), "linuxdo").headers[
            "location"
        ]
    )
    oauth = client.app.state.resources.oauth

    async def race():
        from domainsmanager_application.oauth import OAuthError
        from domainsmanager_application.services import AuthContext

        current = await oauth.auth.authenticate_access_token(
            headers["Authorization"].split()[1]
        )
        results = await asyncio.gather(
            oauth.unlink("github", current, AuthContext()),
            oauth.unlink("linuxdo", current, AuthContext()),
            return_exceptions=True,
        )
        assert sum(result is None for result in results) == 1
        assert any(
            isinstance(result, OAuthError) and result.code == "oauth_last_login_method"
            for result in results
        )
        assert len(await oauth.accounts(current.user.id)) == 1

    client.portal.call(race)
