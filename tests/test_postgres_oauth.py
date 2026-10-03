import asyncio

import pytest
from sqlalchemy import func, select

from domainsmanager_application.oauth import OAuthError, OAuthService
from domainsmanager_persistence.db import run_migrations
from domainsmanager_persistence.models import AppUser, UserAuthIdentity
from tests.api.test_oauth import FakeProvider
from tests.postgres import clean_project_schema, postgres_database
from tests.test_postgres_auth_concurrency import CONTEXT, NOW, make_auth_service

pytestmark = [pytest.mark.postgres, pytest.mark.concurrency]


async def test_oauth_registration_and_last_method_concurrency():
    config = postgres_database()
    await clean_project_schema(config)
    await run_migrations(config)
    engine, auth = make_auth_service(config)
    oauth = OAuthService(
        auth._unit_of_work,
        auth,
        {"github": FakeProvider(), "linuxdo": FakeProvider("linuxdo")},
        "https://example.test",
        "/api/v1",
        clock=lambda: NOW,
    )
    try:
        starts = [await oauth.begin("github") for _ in range(2)]
        results = await asyncio.gather(
            *(
                oauth.complete(
                    "github", state, browser, "test-code", None, None, CONTEXT
                )
                for _, state, browser in starts
            )
        )
        assert results[0][0].user.id == results[1][0].user.id
        async with engine.connect() as connection:
            assert (
                await connection.scalar(select(func.count()).select_from(AppUser)) == 1
            )
            assert (
                await connection.scalar(
                    select(func.count()).select_from(UserAuthIdentity)
                )
                == 1
            )
        result = results[0][0]
        current = await auth.authenticate_access_token(result.tokens.access_token)
        _, state, browser = await oauth.begin("linuxdo", current)
        await oauth.complete(
            "linuxdo",
            state,
            browser,
            "test-code",
            None,
            result.tokens.refresh_token,
            CONTEXT,
        )
        unlinks = await asyncio.gather(
            oauth.unlink("github", current, CONTEXT),
            oauth.unlink("linuxdo", current, CONTEXT),
            return_exceptions=True,
        )
        assert sum(item is None for item in unlinks) == 1
        assert any(
            isinstance(item, OAuthError) and item.code == "oauth_last_login_method"
            for item in unlinks
        )
        assert len(await oauth.accounts(current.user.id)) == 1
    finally:
        await engine.dispose()
