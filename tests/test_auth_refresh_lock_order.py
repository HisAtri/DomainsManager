import asyncio
from pathlib import Path

import pytest
from sqlalchemy import func, select

from domainsmanager_application.services import (
    InvalidTokenError,
    RefreshTokenReplayedError,
    TokenPair,
)
from domainsmanager_persistence.auth import (
    SqlAlchemyAuthSessionRepository,
    SqlAlchemyUnitOfWorkFactory,
)
from domainsmanager_persistence.models import AuthRefreshToken
from domainsmanager_persistence.oauth import SqlAlchemyOAuthRepository
from tests.test_auth_service import CONTEXT, NOW, make_service

pytestmark = [pytest.mark.integration, pytest.mark.concurrency]


async def test_concurrent_refresh_has_one_winner_and_revokes_family(
    tmp_path: Path,
) -> None:
    engine, sessions, service = await make_service(tmp_path)
    try:
        registered = await service.register("refresh-race", "123456", None, CONTEXT)
        results = await asyncio.gather(
            service.rotate_refresh_token(registered.tokens.refresh_token, CONTEXT),
            service.rotate_refresh_token(registered.tokens.refresh_token, CONTEXT),
            return_exceptions=True,
        )
        winners = [result for result in results if isinstance(result, TokenPair)]
        assert len(winners) == 1
        assert sum(isinstance(r, RefreshTokenReplayedError) for r in results) == 1
        with pytest.raises(InvalidTokenError):
            await service.authenticate_access_token(winners[0].access_token)
        with pytest.raises(InvalidTokenError):
            await service.rotate_refresh_token(winners[0].refresh_token, CONTEXT)
        async with sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(AuthRefreshToken))
                == 2
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AuthRefreshToken)
                    .where(AuthRefreshToken.revoked_at.is_(None))
                )
                == 0
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("operation", ["refresh", "logout", "replay"])
async def test_session_mutations_acquire_user_lock_before_other_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    engine, _, service = await make_service(tmp_path)
    try:
        registered = await service.register("lock-order", "123456", None, CONTEXT)
        if operation == "replay":
            await service.rotate_refresh_token(registered.tokens.refresh_token, CONTEXT)

        original_lock = SqlAlchemyOAuthRepository.lock_user
        original_token = SqlAlchemyAuthSessionRepository.get_token
        original_session = SqlAlchemyAuthSessionRepository.get_session
        original_revoke = SqlAlchemyAuthSessionRepository.revoke_session
        locked_transactions = []

        async def lock_user(repository, user_id):
            await original_lock(repository, user_id)
            repository.session.info["user_locked"] = user_id
            locked_transactions.append(repository.session)

        async def get_token(repository, token_id, *, for_update=False):
            if for_update:
                assert repository._session.info.get("user_locked") == registered.user.id
            return await original_token(repository, token_id, for_update=for_update)

        async def get_session(repository, session_id, *, for_update=False):
            if for_update:
                assert repository._session.info.get("user_locked") == registered.user.id
            return await original_session(repository, session_id, for_update=for_update)

        async def revoke_session(repository, session_id, at, reason):
            assert repository._session.info.get("user_locked") == registered.user.id
            await original_revoke(repository, session_id, at, reason)

        monkeypatch.setattr(SqlAlchemyOAuthRepository, "lock_user", lock_user)
        monkeypatch.setattr(SqlAlchemyAuthSessionRepository, "get_token", get_token)
        monkeypatch.setattr(SqlAlchemyAuthSessionRepository, "get_session", get_session)
        monkeypatch.setattr(
            SqlAlchemyAuthSessionRepository, "revoke_session", revoke_session
        )
        if operation == "logout":
            await service.logout(registered.tokens.refresh_token, CONTEXT)
        elif operation == "replay":
            with pytest.raises(RefreshTokenReplayedError):
                await service.rotate_refresh_token(
                    registered.tokens.refresh_token, CONTEXT
                )
        else:
            await service.rotate_refresh_token(registered.tokens.refresh_token, CONTEXT)
        assert len(locked_transactions) == (2 if operation == "replay" else 1)
    finally:
        await engine.dispose()


async def test_refresh_rechecks_revocation_after_read_only_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, sessions, service = await make_service(tmp_path)
    refresh_task = None
    try:
        registered = await service.register("recheck-race", "123456", None, CONTEXT)
        current = await service.authenticate_access_token(
            registered.tokens.access_token
        )
        reached_lock = asyncio.Event()
        resume_refresh = asyncio.Event()
        original_lock = SqlAlchemyOAuthRepository.lock_user

        async def paused_lock(repository, user_id):
            reached_lock.set()
            await resume_refresh.wait()
            await original_lock(repository, user_id)

        monkeypatch.setattr(SqlAlchemyOAuthRepository, "lock_user", paused_lock)
        refresh_task = asyncio.create_task(
            service.rotate_refresh_token(registered.tokens.refresh_token, CONTEXT)
        )
        await asyncio.wait_for(reached_lock.wait(), timeout=5)
        # 在预读与用户锁之间提交凭据变更的撤销，验证刷新不会信任预读状态。
        async with SqlAlchemyUnitOfWorkFactory(sessions)() as uow:
            await original_lock(uow.oauth, registered.user.id)
            await uow.sessions.revoke_session(current.session.id, NOW, "oauth_unlinked")
            await uow.commit()
        resume_refresh.set()
        with pytest.raises(InvalidTokenError):
            await asyncio.wait_for(refresh_task, timeout=5)
        async with sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(AuthRefreshToken))
                == 1
            )
    finally:
        if refresh_task is not None and not refresh_task.done():
            refresh_task.cancel()
            await asyncio.gather(refresh_task, return_exceptions=True)
        await engine.dispose()
