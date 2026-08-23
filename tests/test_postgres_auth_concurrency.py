import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from domainsmanager_application.auth import UserRecord
from domainsmanager_application.security import (
    AccessTokenService,
    PasswordService,
    RefreshTokenService,
)
from domainsmanager_application.services import (
    AuthConfiguration,
    AuthContext,
    AuthService,
    InvalidTokenError,
    RefreshTokenReplayedError,
)
from domainsmanager_persistence.auth import SqlAlchemyUnitOfWorkFactory
from domainsmanager_persistence.db import (
    create_engine,
    create_session_factory,
    run_migrations,
)
from domainsmanager_persistence.models import AuthRefreshToken, SystemState
from tests.postgres import clean_project_schema, postgres_database

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
CONTEXT = AuthContext(request_id="postgres-concurrency", user_agent="pytest")


def make_auth_service(config) -> tuple[object, AuthService]:
    engine = create_engine(config)
    sessions = create_session_factory(engine)
    service = AuthService(
        unit_of_work=SqlAlchemyUnitOfWorkFactory(sessions),
        passwords=PasswordService(),
        access_tokens=AccessTokenService(
            secret="x",
            issuer="issuer",
            audience="audience",
            ttl=timedelta(minutes=15),
            clock_skew=timedelta(0),
        ),
        refresh_tokens=RefreshTokenService("y"),
        configuration=AuthConfiguration(
            registration_enabled=True,
            access_ttl=timedelta(minutes=15),
            refresh_ttl=timedelta(days=30),
        ),
        clock=lambda: NOW,
    )
    return engine, service


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.integration
@pytest.mark.concurrency
async def test_concurrent_refresh_has_one_winner_and_revokes_family() -> None:
    config = postgres_database()
    await clean_project_schema(config)
    await run_migrations(config)
    engine, service = make_auth_service(config)
    try:
        registered = await service.register("refresh-user", "123456", None, CONTEXT)

        results = await asyncio.gather(
            service.rotate_refresh_token(registered.tokens.refresh_token, CONTEXT),
            service.rotate_refresh_token(registered.tokens.refresh_token, CONTEXT),
            return_exceptions=True,
        )

        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], RefreshTokenReplayedError)

        with pytest.raises(InvalidTokenError):
            await service.authenticate_access_token(successes[0].access_token)
        with pytest.raises(InvalidTokenError):
            await service.rotate_refresh_token(successes[0].refresh_token, CONTEXT)

        sessions = create_session_factory(engine)
        async with sessions() as session:
            token_count = await session.scalar(
                select(func.count()).select_from(AuthRefreshToken)
            )
            active_count = await session.scalar(
                select(func.count())
                .select_from(AuthRefreshToken)
                .where(AuthRefreshToken.revoked_at.is_(None))
            )
        assert token_count == 2
        assert active_count == 0
    finally:
        await engine.dispose()
        await clean_project_schema(config)


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.integration
@pytest.mark.concurrency
async def test_concurrent_system_state_claim_has_one_winner() -> None:
    config = postgres_database()
    await clean_project_schema(config)
    await run_migrations(config)
    engine = create_engine(config)
    factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))

    async def claim() -> bool:
        async with factory() as uow:
            claimed = await uow.system_state.try_claim("concurrent-claim", NOW)
            await uow.commit()
            return claimed

    try:
        results = await asyncio.gather(claim(), claim())
        assert sorted(results) == [False, True]
        sessions = create_session_factory(engine)
        async with sessions() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(SystemState)
                .where(SystemState.key == "concurrent-claim")
            )
        assert count == 1
    finally:
        await engine.dispose()
        await clean_project_schema(config)
