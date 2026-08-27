import asyncio

import pytest
from sqlalchemy import func, select

from domainsmanager_api.api.admin import revoke_user_session, set_ban_state
from domainsmanager_application.services import AuthContext
from domainsmanager_persistence.db import create_session_factory, run_migrations
from domainsmanager_persistence.models import SecurityAuditEvent
from tests.postgres import clean_project_schema, postgres_database
from tests.test_postgres_auth_concurrency import CONTEXT, make_auth_service


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.integration
@pytest.mark.concurrency
async def test_admin_state_changes_are_concurrently_idempotent() -> None:
    config = postgres_database()
    await clean_project_schema(config)
    await run_migrations(config)
    engine, service = make_auth_service(config)
    sessions = create_session_factory(engine)
    try:
        admin_result = await service.register("admin-actor", "123456", None, CONTEXT)
        member_result = await service.register("admin-target", "123456", None, CONTEXT)
        admin = await service.authenticate_access_token(
            admin_result.tokens.access_token
        )

        async def change_ban_state(reason: str | None, request_id: str) -> None:
            async with sessions() as session:
                await set_ban_state(
                    session,
                    member_result.user.id,
                    admin_result.user.id,
                    request_id,
                    reason,
                )

        await asyncio.gather(
            change_ban_state("concurrent ban", "ban-1"),
            change_ban_state("concurrent ban", "ban-2"),
        )
        await asyncio.gather(
            change_ban_state(None, "unban-1"),
            change_ban_state(None, "unban-2"),
        )

        member_login = await service.login("admin-target", "123456", CONTEXT)
        member = await service.authenticate_access_token(
            member_login.tokens.access_token
        )

        async def revoke(request_id: str) -> None:
            async with sessions() as session:
                await revoke_user_session(
                    member.user.id,
                    member.session.id,
                    admin,
                    AuthContext(request_id=request_id, user_agent="pytest"),
                    session,
                )

        await asyncio.gather(revoke("revoke-1"), revoke("revoke-2"))

        async with sessions() as session:
            counts = dict(
                (
                    await session.execute(
                        select(SecurityAuditEvent.event_type, func.count())
                        .where(
                            SecurityAuditEvent.event_type.in_(
                                {
                                    "admin.user_banned",
                                    "admin.user_unbanned",
                                    "admin.session_revoked",
                                }
                            )
                        )
                        .group_by(SecurityAuditEvent.event_type)
                    )
                ).all()
            )
        assert counts == {
            "admin.session_revoked": 1,
            "admin.user_banned": 1,
            "admin.user_unbanned": 1,
        }
    finally:
        await engine.dispose()
        await clean_project_schema(config)
