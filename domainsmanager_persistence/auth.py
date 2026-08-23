from __future__ import annotations

from datetime import datetime, timezone
from types import TracebackType
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domainsmanager_application.auth import (
    AuditEvent,
    ConcurrentUpdateError,
    DuplicateRecordError,
    RefreshTokenRecord,
    SessionRecord,
    UserRecord,
)
from domainsmanager_persistence.domains import SqlAlchemyDomainRepository
from domainsmanager_persistence.notifications import SqlAlchemyNotificationRuleRepository
from domainsmanager_persistence.tasks import SqlAlchemyTaskRepository
from domainsmanager_persistence.models import (
    AppUser,
    AuthRefreshToken,
    AuthSession,
    SecurityAuditEvent,
    SystemState,
)


class RecordNotFoundError(RuntimeError):
    pass


def as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count(self) -> int:
        result = await self._session.execute(select(func.count()).select_from(AppUser))
        return result.scalar_one()

    async def get_by_id(
        self,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> UserRecord | None:
        statement = select(AppUser).where(AppUser.id == user_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return self._to_record(row) if row is not None else None

    async def get_by_username(
        self,
        username_normalized: str,
        *,
        for_update: bool = False,
    ) -> UserRecord | None:
        statement = select(AppUser).where(
            AppUser.username_normalized == username_normalized
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return self._to_record(row) if row is not None else None

    async def add(self, user: UserRecord) -> None:
        self._session.add(
            AppUser(
                id=user.id,
                username=user.username,
                username_normalized=user.username_normalized,
                password_hash=user.password_hash,
                email=user.email,
                role=user.role,
                preferences=dict(user.preferences),
                is_active=user.is_active,
                banned_at=user.banned_at,
                password_changed_at=user.password_changed_at,
                last_login_at=user.last_login_at,
                created_at=user.created_at,
                updated_at=user.updated_at,
            )
        )
        try:
            await self._session.flush()
        except IntegrityError as error:
            raise DuplicateRecordError("user already exists") from error

    async def set_last_login(self, user_id: UUID, at: datetime) -> None:
        await self._update_user(user_id, last_login_at=at, updated_at=at)

    async def update_profile(
        self,
        user_id: UUID,
        *,
        email: str | None,
        updated_at: datetime,
    ) -> None:
        await self._update_user(user_id, email=email, updated_at=updated_at)

    async def update_preferences(
        self,
        user_id: UUID,
        preferences: dict,
        updated_at: datetime,
    ) -> None:
        await self._update_user(
            user_id,
            preferences=dict(preferences),
            updated_at=updated_at,
        )

    async def update_password(
        self,
        user_id: UUID,
        password_hash: str,
        changed_at: datetime,
    ) -> None:
        await self._update_user(
            user_id,
            password_hash=password_hash,
            password_changed_at=changed_at,
            updated_at=changed_at,
        )

    async def set_account_state(
        self,
        user_id: UUID,
        *,
        is_active: bool,
        banned_at: datetime | None,
        ban_reason: str | None,
        banned_by_user_id: UUID | None,
        updated_at: datetime,
    ) -> None:
        await self._update_user(
            user_id,
            is_active=is_active,
            banned_at=banned_at,
            ban_reason=ban_reason,
            banned_by_user_id=banned_by_user_id,
            updated_at=updated_at,
        )

    async def _update_user(self, user_id: UUID, **values) -> None:
        result = await self._session.execute(
            update(AppUser).where(AppUser.id == user_id).values(**values)
        )
        if result.rowcount != 1:
            raise RecordNotFoundError(f"user {user_id} does not exist")

    @staticmethod
    def _to_record(row: AppUser) -> UserRecord:
        return UserRecord(
            id=row.id,
            username=row.username,
            username_normalized=row.username_normalized,
            password_hash=row.password_hash,
            email=row.email,
            role=row.role,
            preferences=dict(row.preferences),
            is_active=row.is_active,
            banned_at=as_utc(row.banned_at),
            password_changed_at=as_utc(row.password_changed_at),
            last_login_at=as_utc(row.last_login_at),
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
        )


class SqlAlchemyAuthSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_session(self, session: SessionRecord) -> None:
        self._session.add(
            AuthSession(
                id=session.id,
                user_id=session.user_id,
                created_at=session.created_at,
                last_seen_at=session.last_seen_at,
                absolute_expires_at=session.absolute_expires_at,
                revoked_at=session.revoked_at,
                revoke_reason=session.revoke_reason,
                ip_hash=session.ip_hash,
                user_agent=session.user_agent,
            )
        )
        await self._session.flush()

    async def add_token(self, token: RefreshTokenRecord) -> None:
        self._session.add(
            AuthRefreshToken(
                id=token.id,
                session_id=token.session_id,
                parent_token_id=token.parent_token_id,
                replaced_by_token_id=token.replaced_by_token_id,
                token_hash=token.token_hash,
                issued_at=token.issued_at,
                expires_at=token.expires_at,
                consumed_at=token.consumed_at,
                revoked_at=token.revoked_at,
            )
        )
        try:
            await self._session.flush()
        except IntegrityError as error:
            raise DuplicateRecordError("refresh token already exists") from error

    async def get_session(
        self,
        session_id: UUID,
        *,
        for_update: bool = False,
    ) -> SessionRecord | None:
        statement = select(AuthSession).where(AuthSession.id == session_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return self._session_record(row) if row is not None else None

    async def get_token(
        self,
        token_id: UUID,
        *,
        for_update: bool = False,
    ) -> RefreshTokenRecord | None:
        statement = select(AuthRefreshToken).where(AuthRefreshToken.id == token_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return self._token_record(row) if row is not None else None

    async def consume_token(
        self,
        token_id: UUID,
        replacement_id: UUID,
        consumed_at: datetime,
    ) -> None:
        result = await self._session.execute(
            update(AuthRefreshToken)
            .where(
                AuthRefreshToken.id == token_id,
                AuthRefreshToken.consumed_at.is_(None),
                AuthRefreshToken.revoked_at.is_(None),
            )
            .values(
                consumed_at=consumed_at,
                replaced_by_token_id=replacement_id,
            )
        )
        if result.rowcount != 1:
            raise ConcurrentUpdateError("refresh token is no longer consumable")

    async def touch_session(self, session_id: UUID, at: datetime) -> None:
        result = await self._session.execute(
            update(AuthSession)
            .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
            .values(last_seen_at=at)
        )
        if result.rowcount != 1:
            raise RecordNotFoundError(f"session {session_id} is unavailable")

    async def revoke_session(
        self,
        session_id: UUID,
        at: datetime,
        reason: str,
    ) -> None:
        await self._session.execute(
            update(AuthSession)
            .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=at, revoke_reason=reason)
        )
        await self._session.execute(
            update(AuthRefreshToken)
            .where(
                AuthRefreshToken.session_id == session_id,
                AuthRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=at)
        )

    async def revoke_other_sessions(
        self,
        user_id: UUID,
        current_session_id: UUID,
        at: datetime,
        reason: str,
    ) -> None:
        await self._revoke_user_sessions(
            user_id,
            at,
            reason,
            exclude_session_id=current_session_id,
        )

    async def revoke_all_sessions(
        self,
        user_id: UUID,
        at: datetime,
        reason: str,
    ) -> None:
        await self._revoke_user_sessions(user_id, at, reason)

    async def _revoke_user_sessions(
        self,
        user_id: UUID,
        at: datetime,
        reason: str,
        *,
        exclude_session_id: UUID | None = None,
    ) -> None:
        session_ids = select(AuthSession.id).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
        sessions = update(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
        if exclude_session_id is not None:
            session_ids = session_ids.where(AuthSession.id != exclude_session_id)
            sessions = sessions.where(AuthSession.id != exclude_session_id)
        await self._session.execute(
            update(AuthRefreshToken)
            .where(
                AuthRefreshToken.session_id.in_(session_ids),
                AuthRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=at)
        )
        await self._session.execute(
            sessions.values(revoked_at=at, revoke_reason=reason)
        )

    @staticmethod
    def _session_record(row: AuthSession) -> SessionRecord:
        return SessionRecord(
            id=row.id,
            user_id=row.user_id,
            created_at=as_utc(row.created_at),
            last_seen_at=as_utc(row.last_seen_at),
            absolute_expires_at=as_utc(row.absolute_expires_at),
            revoked_at=as_utc(row.revoked_at),
            revoke_reason=row.revoke_reason,
            ip_hash=row.ip_hash,
            user_agent=row.user_agent,
        )

    @staticmethod
    def _token_record(row: AuthRefreshToken) -> RefreshTokenRecord:
        return RefreshTokenRecord(
            id=row.id,
            session_id=row.session_id,
            parent_token_id=row.parent_token_id,
            replaced_by_token_id=row.replaced_by_token_id,
            token_hash=bytes(row.token_hash),
            issued_at=as_utc(row.issued_at),
            expires_at=as_utc(row.expires_at),
            consumed_at=as_utc(row.consumed_at),
            revoked_at=as_utc(row.revoked_at),
        )


class SqlAlchemyAuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: AuditEvent) -> None:
        self._session.add(
            SecurityAuditEvent(
                id=uuid4(),
                actor_user_id=event.actor_user_id,
                event_type=event.event_type,
                target_type=event.target_type,
                target_id=event.target_id,
                request_id=event.request_id,
                ip_hash=event.ip_hash,
                event_metadata=dict(event.metadata),
                occurred_at=event.occurred_at,
            )
        )
        await self._session.flush()


class SqlAlchemySystemStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def try_claim(self, key: str, created_at: datetime) -> bool:
        try:
            async with self._session.begin_nested():
                self._session.add(SystemState(key=key, created_at=created_at))
                await self._session.flush()
        except IntegrityError:
            return False
        return True


class SqlAlchemyUnitOfWork:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._session: AsyncSession | None = None
        self._transaction = None

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        self._session = self._sessions()
        self._transaction = await self._session.begin()
        self.users = SqlAlchemyUserRepository(self._session)
        self.sessions = SqlAlchemyAuthSessionRepository(self._session)
        self.audits = SqlAlchemyAuditRepository(self._session)
        self.system_state = SqlAlchemySystemStateRepository(self._session)
        self.domains = SqlAlchemyDomainRepository(self._session)
        self.tasks = SqlAlchemyTaskRepository(self._session)
        self.notifications = SqlAlchemyNotificationRuleRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        if self._session is None or self._transaction is None:
            return
        if exc_type is not None or self._transaction.is_active:
            await self._transaction.rollback()
        await self._session.close()
        self._session = None
        self._transaction = None

    async def commit(self) -> None:
        if self._transaction is None:
            raise RuntimeError("unit of work is not active")
        await self._transaction.commit()

    async def rollback(self) -> None:
        if self._transaction is None:
            raise RuntimeError("unit of work is not active")
        await self._transaction.rollback()


class SqlAlchemyUnitOfWorkFactory:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    def __call__(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self._sessions)
