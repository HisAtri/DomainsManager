from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from domainsmanager_api.global_setting_registry import GLOBAL_SETTING_BY_KEY
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.models import (
    AppUser,
    EmailVerificationChallenge,
    GlobalSetting,
)

TOKEN_TTL = timedelta(minutes=30)
RESEND_COOLDOWN = timedelta(seconds=60)


async def setting_values(session: AsyncSession, settings: Settings) -> dict[str, object]:
    keys = ("email_verification_enabled", "email_domain_allowlist", "site_url")
    rows = {
        row.key: row.value
        for row in (await session.execute(select(GlobalSetting).where(GlobalSetting.key.in_(keys)))).scalars()
    }
    return {
        key: rows.get(key, GLOBAL_SETTING_BY_KEY[key].default(settings)) for key in keys
    }


def validate_site_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("site URL must be an absolute HTTP(S) URL without credentials or fragment")
    return value.strip().rstrip("/")


def validate_allowlist(email: str, raw: str) -> None:
    entries = [line.strip().lower() for line in raw.splitlines() if line.strip()]
    if not entries:
        return
    domain = "@" + email.rsplit("@", 1)[-1].lower()
    if domain not in entries:
        raise HTTPException(status_code=422, detail={"code": "email_domain_not_allowed", "message": "email domain is not allowed"})


async def begin(
    session: AsyncSession, *, user_id: UUID, email: str, site_url: str
) -> str:
    now = datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    await session.execute(
        update(EmailVerificationChallenge)
        .where(EmailVerificationChallenge.user_id == user_id, EmailVerificationChallenge.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    session.add(EmailVerificationChallenge(
        id=uuid4(), user_id=user_id, email=email,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        created_at=now, expires_at=now + TOKEN_TTL,
    ))
    await session.execute(update(AppUser).where(AppUser.id == user_id).values(pending_email=email, email_verified_at=None, updated_at=now))
    await session.commit()
    return f"{site_url}/email/verify#token={token}"


async def resend_available(session: AsyncSession, user_id: UUID) -> bool:
    latest = await session.scalar(
        select(EmailVerificationChallenge.created_at)
        .where(
            EmailVerificationChallenge.user_id == user_id,
            EmailVerificationChallenge.consumed_at.is_(None),
        )
        .order_by(EmailVerificationChallenge.created_at.desc())
        .limit(1)
    )
    if latest is not None and latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    return latest is None or latest <= datetime.now(UTC) - RESEND_COOLDOWN


async def confirm(session: AsyncSession, token: str) -> AppUser:
    now = datetime.now(UTC)
    row = (await session.execute(
        select(EmailVerificationChallenge).where(
            EmailVerificationChallenge.token_hash == hashlib.sha256(token.encode()).hexdigest(),
            EmailVerificationChallenge.consumed_at.is_(None),
            EmailVerificationChallenge.expires_at >= now,
        ).with_for_update()
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=422, detail={"code": "invalid_email_verification_token", "message": "verification link is invalid or expired"})
    user = await session.get(AppUser, row.user_id, with_for_update=True)
    if user is None or user.pending_email != row.email:
        raise HTTPException(status_code=422, detail={"code": "invalid_email_verification_token", "message": "verification link is no longer valid"})
    user.email, user.pending_email, user.email_verified_at, user.updated_at = row.email, None, now, now
    row.consumed_at = now
    await session.commit()
    return user
