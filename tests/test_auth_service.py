from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from domainsmanager_application.security import (
    AccessTokenService,
    PasswordService,
    RefreshTokenService,
)
from domainsmanager_application.services import (
    AccountBannedError,
    AuthConfiguration,
    AuthContext,
    AuthService,
    AuthenticationError,
    InvalidTokenError,
    PasswordReusedError,
    RefreshTokenReplayedError,
    RegistrationDisabledError,
    UsernameTakenError,
)
from domainsmanager_persistence.auth import SqlAlchemyUnitOfWorkFactory
from domainsmanager_persistence.db import (
    create_engine,
    create_session_factory,
    run_migrations,
)
from tests.database import sqlite_database

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
CONTEXT = AuthContext(request_id="request-1234", user_agent="test-client")


async def make_service(
    tmp_path: Path,
    *,
    registration_enabled: bool = True,
    passwords: PasswordService | None = None,
):
    database = sqlite_database(tmp_path / "service.db")
    await run_migrations(database)
    engine = create_engine(database)
    sessions = create_session_factory(engine)
    service = AuthService(
        unit_of_work=SqlAlchemyUnitOfWorkFactory(sessions),
        passwords=passwords or PasswordService(),
        access_tokens=AccessTokenService(
            secret="x",
            issuer="issuer",
            audience="audience",
            ttl=timedelta(minutes=15),
            clock_skew=timedelta(0),
        ),
        refresh_tokens=RefreshTokenService("y"),
        configuration=AuthConfiguration(
            registration_enabled=registration_enabled,
            access_ttl=timedelta(minutes=15),
            refresh_ttl=timedelta(days=30),
        ),
        clock=lambda: NOW,
    )
    return engine, sessions, service


@pytest.mark.asyncio
@pytest.mark.integration
async def test_register_login_and_access_authentication(tmp_path: Path) -> None:
    engine, _, service = await make_service(tmp_path)

    registered = await service.register(
        "Test.User",
        "123456",
        "user@example.com",
        CONTEXT,
    )
    authenticated = await service.authenticate_access_token(
        registered.tokens.access_token
    )
    logged_in = await service.login("test.user", "123456", CONTEXT)

    assert registered.user.username == "Test.User"
    assert authenticated.user.id == registered.user.id
    assert logged_in.user.id == registered.user.id
    with pytest.raises(UsernameTakenError):
        await service.register("test.USER", "123456", None, CONTEXT)
    with pytest.raises(AuthenticationError):
        await service.login("test.user", "wrong-password", CONTEXT)

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_registration_can_be_disabled(tmp_path: Path) -> None:
    engine, _, service = await make_service(tmp_path, registration_enabled=False)

    with pytest.raises(RegistrationDisabledError):
        await service.register("test-user", "123456", None, CONTEXT)

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refresh_rotation_replay_revokes_session(tmp_path: Path) -> None:
    engine, _, service = await make_service(tmp_path)
    registered = await service.register("test-user", "123456", None, CONTEXT)

    rotated = await service.rotate_refresh_token(
        registered.tokens.refresh_token,
        CONTEXT,
    )
    await service.authenticate_access_token(rotated.access_token)

    with pytest.raises(RefreshTokenReplayedError):
        await service.rotate_refresh_token(
            registered.tokens.refresh_token,
            CONTEXT,
        )
    with pytest.raises(InvalidTokenError):
        await service.authenticate_access_token(rotated.access_token)
    with pytest.raises(InvalidTokenError):
        await service.rotate_refresh_token(rotated.refresh_token, CONTEXT)

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_logout_is_idempotent_and_revokes_access(tmp_path: Path) -> None:
    engine, _, service = await make_service(tmp_path)
    registered = await service.register("test-user", "123456", None, CONTEXT)

    await service.logout(registered.tokens.refresh_token, CONTEXT)
    await service.logout(registered.tokens.refresh_token, CONTEXT)
    await service.logout("invalid", CONTEXT)

    with pytest.raises(InvalidTokenError):
        await service.authenticate_access_token(registered.tokens.access_token)

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_password_change_keeps_current_session_and_revokes_others(
    tmp_path: Path,
) -> None:
    engine, _, service = await make_service(tmp_path)
    registered = await service.register("test-user", "123456", None, CONTEXT)
    other = await service.login("test-user", "123456", CONTEXT)
    authenticated = await service.authenticate_access_token(
        registered.tokens.access_token
    )

    with pytest.raises(PasswordReusedError):
        await service.change_password(
            authenticated,
            "123456",
            "123456",
            CONTEXT,
        )
    await service.change_password(
        authenticated,
        "123456",
        "654321",
        CONTEXT,
    )

    with pytest.raises(InvalidTokenError):
        await service.authenticate_access_token(registered.tokens.access_token)
    with pytest.raises(InvalidTokenError):
        await service.authenticate_access_token(other.tokens.access_token)
    with pytest.raises(AuthenticationError):
        await service.login("test-user", "123456", CONTEXT)
    await service.login("test-user", "654321", CONTEXT)

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_profile_settings_and_ban_take_effect_immediately(tmp_path: Path) -> None:
    engine, sessions, service = await make_service(tmp_path)
    registered = await service.register("test-user", "123456", None, CONTEXT)

    profile = await service.update_profile(
        registered.user.id,
        "new@example.com",
        CONTEXT,
    )
    settings = await service.update_settings(
        registered.user.id,
        {"locale": "zh-CN", "timezone": "UTC"},
        CONTEXT,
    )

    assert profile.email == "new@example.com"
    assert settings.preferences == {"locale": "zh-CN", "timezone": "UTC"}

    factory = SqlAlchemyUnitOfWorkFactory(sessions)
    async with factory() as uow:
        await uow.users.set_account_state(
            registered.user.id,
            is_active=False,
            banned_at=NOW,
            ban_reason="test ban",
            banned_by_user_id=None,
            updated_at=NOW,
        )
        await uow.sessions.revoke_all_sessions(
            registered.user.id,
            NOW,
            "account_banned",
        )
        await uow.commit()

    with pytest.raises(AuthenticationError):
        await service.login("test-user", "123456", CONTEXT)
    with pytest.raises(AccountBannedError):
        await service.authenticate_access_token(registered.tokens.access_token)

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bootstrap_admin_only_uses_credentials_for_empty_database(
    tmp_path: Path,
) -> None:
    engine, _, service = await make_service(tmp_path)

    assert await service.bootstrap_first_admin("admin", "123456") is True
    assert await service.bootstrap_first_admin("other-admin", "654321") is False
    admin = await service.login("admin", "123456", CONTEXT)
    assert admin.user.role == "admin"

    await engine.dispose()


class FailingPasswordService(PasswordService):
    def hash(self, password: str) -> str:
        raise AssertionError("password must not be used when users already exist")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bootstrap_skips_password_when_database_has_user(tmp_path: Path) -> None:
    engine, sessions, service = await make_service(tmp_path)
    await service.register("existing", "123456", None, CONTEXT)
    guarded = AuthService(
        unit_of_work=SqlAlchemyUnitOfWorkFactory(sessions),
        passwords=FailingPasswordService(),
        access_tokens=AccessTokenService(
            secret="x",
            issuer="issuer",
            audience="audience",
            ttl=timedelta(minutes=15),
        ),
        refresh_tokens=RefreshTokenService("y"),
        configuration=AuthConfiguration(
            registration_enabled=True,
            access_ttl=timedelta(minutes=15),
            refresh_ttl=timedelta(days=30),
        ),
        clock=lambda: NOW,
    )

    assert await guarded.bootstrap_first_admin("admin", "secret") is False

    await engine.dispose()
