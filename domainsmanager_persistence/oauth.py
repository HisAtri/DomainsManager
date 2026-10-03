from dataclasses import asdict
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from domainsmanager_application.auth import DuplicateRecordError
from domainsmanager_application.oauth import AuthorizationAttempt, IdentityRecord
from domainsmanager_persistence.models import (
    AppUser,
    OAuthAuthorizationAttempt,
    UserAuthIdentity,
)


def record(kind, row):
    from domainsmanager_persistence.auth import as_utc

    return kind(
        **{
            name: as_utc(value)
            if isinstance(value := getattr(row, name), datetime)
            else value
            for name in kind.__dataclass_fields__
        }
    )


class SqlAlchemyOAuthRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_attempt(self, attempt: AuthorizationAttempt) -> None:
        self.session.add(OAuthAuthorizationAttempt(**asdict(attempt)))
        await self.session.flush()

    async def consume_attempt(
        self, provider: str, state_hash: str, browser_hash: str, now: datetime
    ) -> AuthorizationAttempt | None:
        row = (
            await self.session.execute(
                update(OAuthAuthorizationAttempt)
                .where(
                    OAuthAuthorizationAttempt.provider_key == provider,
                    OAuthAuthorizationAttempt.state_hash == state_hash,
                    OAuthAuthorizationAttempt.browser_hash == browser_hash,
                    OAuthAuthorizationAttempt.expires_at > now,
                    OAuthAuthorizationAttempt.consumed_at.is_(None),
                )
                .values(consumed_at=now)
                .returning(OAuthAuthorizationAttempt)
            )
        ).scalar_one_or_none()
        return record(AuthorizationAttempt, row) if row else None

    async def cleanup(self, now: datetime) -> None:
        await self.session.execute(
            delete(OAuthAuthorizationAttempt).where(
                OAuthAuthorizationAttempt.expires_at <= now
            )
        )

    async def get_identity(self, provider: str, subject: str) -> IdentityRecord | None:
        row = (
            await self.session.execute(
                select(UserAuthIdentity)
                .where(
                    UserAuthIdentity.provider_key == provider,
                    UserAuthIdentity.provider_subject == subject,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        return record(IdentityRecord, row) if row else None

    async def list_identities(self, user_id: UUID) -> list[IdentityRecord]:
        rows = (
            await self.session.execute(
                select(UserAuthIdentity)
                .where(UserAuthIdentity.user_id == user_id)
                .order_by(UserAuthIdentity.provider_key)
            )
        ).scalars()
        return [record(IdentityRecord, row) for row in rows]

    async def add_identity(self, identity: IdentityRecord) -> None:
        self.session.add(UserAuthIdentity(**asdict(identity)))
        try:
            await self.session.flush()
        except IntegrityError as error:
            raise DuplicateRecordError("identity already linked") from error

    async def update_identity(self, identity: IdentityRecord) -> None:
        values = asdict(identity)
        values.pop("id")
        await self.session.execute(
            update(UserAuthIdentity)
            .where(UserAuthIdentity.id == identity.id)
            .values(**values)
        )

    async def delete_identity(self, identity_id: UUID) -> None:
        await self.session.execute(
            delete(UserAuthIdentity).where(UserAuthIdentity.id == identity_id)
        )

    async def lock_user(self, user_id: UUID) -> None:
        # SQLite 忽略 FOR UPDATE；无值变更的 UPDATE 在两种数据库都能串行化登录方式变更。
        await self.session.execute(
            update(AppUser)
            .where(AppUser.id == user_id)
            .values(updated_at=AppUser.updated_at)
        )

    async def enable_password(self, user_id: UUID) -> None:
        await self.session.execute(
            update(AppUser)
            .where(AppUser.id == user_id)
            .values(password_auth_enabled=True)
        )
