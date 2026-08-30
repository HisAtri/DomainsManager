from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from domainsmanager_api.email_verification import (
    begin,
    confirm,
    resend_available,
    validate_allowlist,
    validate_site_url,
)
from domainsmanager_persistence.db import create_engine, create_session_factory, run_migrations
from domainsmanager_persistence.models import AppUser
from tests.database import sqlite_database


@pytest.mark.asyncio
async def test_verification_promotes_pending_email_once(tmp_path: Path) -> None:
    database = sqlite_database(tmp_path / "verification.db")
    await run_migrations(database)
    engine = create_engine(database)
    sessions = create_session_factory(engine)
    user_id = uuid4()
    now = datetime(2026, 8, 30, tzinfo=UTC)
    try:
        async with sessions() as session:
            session.add(AppUser(id=user_id, username="verify", username_normalized="verify", password_hash="hash", email="old@example.test", role="user", preferences={}, is_active=True, password_changed_at=now, created_at=now, updated_at=now))
            await session.commit()
        async with sessions() as session:
            link = await begin(session, user_id=user_id, email="new@example.test", site_url="https://console.example.test")
            token = link.split("#token=", 1)[1]
        async with sessions() as session:
            assert not await resend_available(session, user_id)
        async with sessions() as session:
            user = await confirm(session, token)
            assert user.email == "new@example.test"
            assert user.pending_email is None
            assert user.email_verified_at is not None
        async with sessions() as session:
            assert (await session.execute(select(AppUser.email).where(AppUser.id == user_id))).scalar_one() == "new@example.test"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_resending_keeps_previous_verification_links_valid(tmp_path: Path) -> None:
    database = sqlite_database(tmp_path / "verification-resend.db")
    await run_migrations(database)
    engine = create_engine(database)
    sessions = create_session_factory(engine)
    user_id = uuid4()
    now = datetime(2026, 8, 30, tzinfo=UTC)
    try:
        async with sessions() as session:
            session.add(AppUser(id=user_id, username="resend", username_normalized="resend", password_hash="hash", email=None, role="user", preferences={}, is_active=True, password_changed_at=now, created_at=now, updated_at=now))
            await session.commit()
        async with sessions() as session:
            first = await begin(session, user_id=user_id, email="new@example.test", site_url="https://console.example.test")
        async with sessions() as session:
            second = await begin(session, user_id=user_id, email="new@example.test", site_url="https://console.example.test")
        assert first != second
        async with sessions() as session:
            user = await confirm(session, first.split("#token=", 1)[1])
            assert user.email == "new@example.test"
    finally:
        await engine.dispose()


def test_allowlist_and_site_url_validation() -> None:
    validate_allowlist("person@gmail.com", "@gmail.com\n@example.org")
    with pytest.raises(Exception):
        validate_allowlist("person@invalid.test", "@gmail.com")
    assert validate_site_url("https://console.example.test/") == "https://console.example.test"
    with pytest.raises(ValueError):
        validate_site_url("https://user:pass@example.test")
