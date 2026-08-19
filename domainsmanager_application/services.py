from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import UUID, uuid4

from domainsmanager_application.auth import (
    AuditEvent,
    ConcurrentUpdateError,
    DuplicateRecordError,
    RefreshTokenRecord,
    SessionRecord,
    UnitOfWorkFactory,
    UserRecord,
)
from domainsmanager_application.security import (
    AccessTokenService,
    InvalidAccessTokenError,
    InvalidRefreshTokenError,
    PasswordService,
    RefreshTokenService,
    normalize_username,
    utc_now,
    validate_password,
)


class AuthenticationError(RuntimeError):
    code = "invalid_credentials"


class InvalidTokenError(AuthenticationError):
    code = "invalid_token"


class RefreshTokenReplayedError(AuthenticationError):
    code = "refresh_token_replayed"


class RegistrationDisabledError(RuntimeError):
    code = "registration_disabled"


class UsernameTakenError(RuntimeError):
    code = "username_taken"


class AccountBannedError(RuntimeError):
    code = "account_banned"


class PasswordMismatchError(RuntimeError):
    code = "password_mismatch"


class PasswordReusedError(RuntimeError):
    code = "password_reused"


@dataclass(frozen=True, slots=True)
class AuthContext:
    request_id: str | None = None
    ip_hash: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    user: UserRecord
    tokens: TokenPair


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user: UserRecord
    session: SessionRecord


@dataclass(frozen=True, slots=True)
class AuthConfiguration:
    registration_enabled: bool
    access_ttl: timedelta
    refresh_ttl: timedelta


class AuthService:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        passwords: PasswordService,
        access_tokens: AccessTokenService,
        refresh_tokens: RefreshTokenService,
        configuration: AuthConfiguration,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._passwords = passwords
        self._access_tokens = access_tokens
        self._refresh_tokens = refresh_tokens
        self._configuration = configuration
        self._clock = clock

    async def register(
        self,
        username: str,
        password: str,
        email: str | None,
        context: AuthContext,
    ) -> AuthenticationResult:
        if not self._configuration.registration_enabled:
            raise RegistrationDisabledError("registration is disabled")
        display, normalized = normalize_username(username)
        password_hash = await self._hash_password(password)
        now = self._clock()
        user = UserRecord(
            id=uuid4(),
            username=display,
            username_normalized=normalized,
            password_hash=password_hash,
            email=email,
            role="user",
            preferences={},
            is_active=True,
            banned_at=None,
            password_changed_at=now,
            last_login_at=None,
            created_at=now,
            updated_at=now,
        )
        try:
            async with self._unit_of_work() as uow:
                if await uow.users.get_by_username(normalized) is not None:
                    raise UsernameTakenError("username is already in use")
                await uow.users.add(user)
                result = await self._create_session(uow, user, context, now)
                await uow.audits.add(
                    self._audit("user.registered", now, context, user.id, user.id)
                )
                await uow.commit()
                return result
        except DuplicateRecordError as error:
            raise UsernameTakenError("username is already in use") from error

    async def login(
        self,
        username: str,
        password: str,
        context: AuthContext,
    ) -> AuthenticationResult:
        try:
            _, normalized = normalize_username(username)
        except ValueError:
            await self._verify_dummy_password(password)
            raise AuthenticationError("credentials are invalid") from None

        async with self._unit_of_work() as uow:
            user = await uow.users.get_by_username(normalized)
            if user is None:
                await self._verify_dummy_password(password)
                raise AuthenticationError("credentials are invalid")
            if not await self._verify_password(password, user.password_hash):
                raise AuthenticationError("credentials are invalid")
            if not user.is_active or user.banned_at is not None:
                raise AuthenticationError("credentials are invalid")
            now = self._clock()
            await uow.users.set_last_login(user.id, now)
            result = await self._create_session(uow, user, context, now)
            await uow.audits.add(
                self._audit("session.created", now, context, user.id, user.id)
            )
            await uow.commit()
            return AuthenticationResult(
                user=self._replace_user_last_login(result.user, now),
                tokens=result.tokens,
            )

    async def rotate_refresh_token(
        self,
        value: str,
        context: AuthContext,
    ) -> TokenPair:
        try:
            token_id, digest = self._refresh_tokens.parse(value)
        except InvalidRefreshTokenError as error:
            raise InvalidTokenError("refresh token is invalid") from error

        replay_session_id: UUID | None = None
        try:
            async with self._unit_of_work() as uow:
                token = await uow.sessions.get_token(token_id, for_update=True)
                if token is None or not self._refresh_tokens.matches(
                    token.token_hash, digest
                ):
                    raise InvalidTokenError("refresh token is invalid")
                session = await uow.sessions.get_session(
                    token.session_id,
                    for_update=True,
                )
                now = self._clock()
                if token.consumed_at is not None:
                    replay_session_id = token.session_id
                    raise RefreshTokenReplayedError("refresh token was replayed")
                if not self._token_is_active(token, session, now):
                    raise InvalidTokenError("refresh token is unavailable")
                user = await uow.users.get_by_id(session.user_id, for_update=True)
                if user is None:
                    raise InvalidTokenError("refresh token is unavailable")
                self._ensure_user_active(user)

                issued = self._refresh_tokens.issue()
                replacement = RefreshTokenRecord(
                    id=issued.token_id,
                    session_id=session.id,
                    parent_token_id=token.id,
                    replaced_by_token_id=None,
                    token_hash=issued.digest,
                    issued_at=now,
                    expires_at=min(
                        now + self._configuration.refresh_ttl,
                        session.absolute_expires_at,
                    ),
                    consumed_at=None,
                    revoked_at=None,
                )
                await uow.sessions.add_token(replacement)
                await uow.sessions.consume_token(token.id, replacement.id, now)
                await uow.sessions.touch_session(session.id, now)
                await uow.audits.add(
                    self._audit(
                        "session.refreshed",
                        now,
                        context,
                        user.id,
                        session.id,
                        target_type="session",
                    )
                )
                await uow.commit()
                return self._token_pair(user, session.id, issued.value, now)
        except (ConcurrentUpdateError, DuplicateRecordError):
            replay_session_id = await self._session_id_for_token(token_id)
        except RefreshTokenReplayedError:
            pass

        if replay_session_id is not None:
            await self._revoke_replayed_session(replay_session_id, context)
            raise RefreshTokenReplayedError("refresh token was replayed")
        raise InvalidTokenError("refresh token is invalid")

    async def logout(self, value: str, context: AuthContext) -> None:
        try:
            token_id, digest = self._refresh_tokens.parse(value)
        except InvalidRefreshTokenError:
            return
        async with self._unit_of_work() as uow:
            token = await uow.sessions.get_token(token_id)
            if token is None or not self._refresh_tokens.matches(
                token.token_hash, digest
            ):
                return
            session = await uow.sessions.get_session(token.session_id)
            if session is None or session.revoked_at is not None:
                return
            now = self._clock()
            await uow.sessions.revoke_session(session.id, now, "logout")
            await uow.audits.add(
                self._audit(
                    "session.revoked",
                    now,
                    context,
                    session.user_id,
                    session.id,
                    target_type="session",
                )
            )
            await uow.commit()

    async def authenticate_access_token(self, token: str) -> AuthenticatedUser:
        try:
            claims = self._access_tokens.decode(token, self._clock())
        except InvalidAccessTokenError as error:
            raise InvalidTokenError("access token is invalid") from error
        async with self._unit_of_work() as uow:
            user = await uow.users.get_by_id(claims.user_id)
            session = await uow.sessions.get_session(claims.session_id)
            now = self._clock()
            if user is None or session is None or session.user_id != claims.user_id:
                raise InvalidTokenError("access token is invalid")
            self._ensure_user_active(user)
            if session.revoked_at is not None or session.absolute_expires_at <= now:
                raise InvalidTokenError("session is unavailable")
            if claims.password_version != int(
                user.password_changed_at.timestamp() * 1_000_000
            ):
                raise InvalidTokenError("access token predates password change")
            return AuthenticatedUser(user=user, session=session)

    async def get_user(self, user_id: UUID) -> UserRecord:
        async with self._unit_of_work() as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise InvalidTokenError("user is unavailable")
            self._ensure_user_active(user)
            return user

    async def update_profile(
        self,
        user_id: UUID,
        email: str | None,
        context: AuthContext,
    ) -> UserRecord:
        now = self._clock()
        async with self._unit_of_work() as uow:
            user = await uow.users.get_by_id(user_id, for_update=True)
            if user is None:
                raise InvalidTokenError("user is unavailable")
            self._ensure_user_active(user)
            await uow.users.update_profile(user.id, email=email, updated_at=now)
            await uow.audits.add(
                self._audit("user.profile_updated", now, context, user.id, user.id)
            )
            await uow.commit()
        return await self.get_user(user_id)

    async def update_settings(
        self,
        user_id: UUID,
        preferences: dict[str, Any],
        context: AuthContext,
    ) -> UserRecord:
        now = self._clock()
        async with self._unit_of_work() as uow:
            user = await uow.users.get_by_id(user_id, for_update=True)
            if user is None:
                raise InvalidTokenError("user is unavailable")
            self._ensure_user_active(user)
            await uow.users.update_preferences(user.id, preferences, now)
            await uow.audits.add(
                self._audit("user.settings_updated", now, context, user.id, user.id)
            )
            await uow.commit()
        return await self.get_user(user_id)

    async def change_password(
        self,
        authenticated: AuthenticatedUser,
        current_password: str,
        new_password: str,
        context: AuthContext,
    ) -> None:
        validate_password(new_password)
        original_hash = authenticated.user.password_hash
        if not await self._verify_password(current_password, original_hash):
            raise PasswordMismatchError("current password is incorrect")
        if await self._verify_password(new_password, original_hash):
            raise PasswordReusedError("new password must differ from current password")
        new_hash = await self._hash_password(new_password)

        async with self._unit_of_work() as uow:
            user = await uow.users.get_by_id(
                authenticated.user.id,
                for_update=True,
            )
            if user is None or user.password_hash != original_hash:
                raise InvalidTokenError("user credentials have changed")
            self._ensure_user_active(user)
            now = max(
                self._clock(),
                user.password_changed_at + timedelta(microseconds=1),
            )
            await uow.users.update_password(user.id, new_hash, now)
            await uow.sessions.revoke_other_sessions(
                user.id,
                authenticated.session.id,
                now,
                "password_changed",
            )
            await uow.audits.add(
                self._audit("user.password_changed", now, context, user.id, user.id)
            )
            await uow.commit()

    async def bootstrap_first_admin(
        self,
        username: str,
        password: str,
    ) -> bool:
        async with self._unit_of_work() as uow:
            if await uow.users.count() != 0:
                return False

        display, normalized = normalize_username(username)
        password_hash = await self._hash_password(password)
        now = self._clock()
        user = UserRecord(
            id=uuid4(),
            username=display,
            username_normalized=normalized,
            password_hash=password_hash,
            email=None,
            role="admin",
            preferences={},
            is_active=True,
            banned_at=None,
            password_changed_at=now,
            last_login_at=None,
            created_at=now,
            updated_at=now,
        )
        try:
            async with self._unit_of_work() as uow:
                if not await uow.system_state.try_claim("bootstrap_admin", now):
                    return False
                if await uow.users.count() != 0:
                    await uow.commit()
                    return False
                await uow.users.add(user)
                await uow.audits.add(
                    AuditEvent(
                        event_type="user.bootstrap_admin_created",
                        occurred_at=now,
                        target_type="user",
                        target_id=user.id,
                    )
                )
                await uow.commit()
                return True
        except DuplicateRecordError:
            return False

    async def _create_session(
        self,
        uow,
        user: UserRecord,
        context: AuthContext,
        now: datetime,
    ) -> AuthenticationResult:
        session_id = uuid4()
        absolute_expires_at = now + self._configuration.refresh_ttl
        session = SessionRecord(
            id=session_id,
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            absolute_expires_at=absolute_expires_at,
            revoked_at=None,
            revoke_reason=None,
            ip_hash=context.ip_hash,
            user_agent=(context.user_agent or "")[:512] or None,
        )
        issued = self._refresh_tokens.issue()
        refresh = RefreshTokenRecord(
            id=issued.token_id,
            session_id=session_id,
            parent_token_id=None,
            replaced_by_token_id=None,
            token_hash=issued.digest,
            issued_at=now,
            expires_at=absolute_expires_at,
            consumed_at=None,
            revoked_at=None,
        )
        await uow.sessions.add_session(session)
        await uow.sessions.add_token(refresh)
        return AuthenticationResult(
            user=user,
            tokens=self._token_pair(user, session.id, issued.value, now),
        )

    def _token_pair(
        self,
        user: UserRecord,
        session_id: UUID,
        refresh_value: str,
        now: datetime,
    ) -> TokenPair:
        return TokenPair(
            access_token=self._access_tokens.issue(
                user.id,
                session_id,
                user.password_changed_at,
                now,
            ),
            refresh_token=refresh_value,
            expires_in=int(self._configuration.access_ttl.total_seconds()),
        )

    async def _session_id_for_token(self, token_id: UUID) -> UUID | None:
        async with self._unit_of_work() as uow:
            token = await uow.sessions.get_token(token_id)
            return token.session_id if token is not None else None

    async def _revoke_replayed_session(
        self,
        session_id: UUID,
        context: AuthContext,
    ) -> None:
        now = self._clock()
        async with self._unit_of_work() as uow:
            session = await uow.sessions.get_session(session_id, for_update=True)
            if session is None:
                return
            await uow.sessions.revoke_session(
                session.id,
                now,
                "refresh_token_replayed",
            )
            await uow.audits.add(
                self._audit(
                    "session.refresh_replayed",
                    now,
                    context,
                    session.user_id,
                    session.id,
                    target_type="session",
                )
            )
            await uow.commit()

    @staticmethod
    def _token_is_active(
        token: RefreshTokenRecord,
        session: SessionRecord,
        now: datetime,
    ) -> bool:
        return (
            token.revoked_at is None
            and token.expires_at > now
            and session.revoked_at is None
            and session.absolute_expires_at > now
        )

    @staticmethod
    def _ensure_user_active(user: UserRecord) -> None:
        if not user.is_active or user.banned_at is not None:
            raise AccountBannedError("account is unavailable")

    @staticmethod
    def _replace_user_last_login(user: UserRecord, at: datetime) -> UserRecord:
        return UserRecord(
            id=user.id,
            username=user.username,
            username_normalized=user.username_normalized,
            password_hash=user.password_hash,
            email=user.email,
            role=user.role,
            preferences=user.preferences,
            is_active=user.is_active,
            banned_at=user.banned_at,
            password_changed_at=user.password_changed_at,
            last_login_at=at,
            created_at=user.created_at,
            updated_at=at,
        )

    async def _hash_password(self, password: str) -> str:
        return await asyncio.to_thread(self._passwords.hash, password)

    async def _verify_password(self, password: str, password_hash: str) -> bool:
        return await asyncio.to_thread(
            self._passwords.verify,
            password,
            password_hash,
        )

    async def _verify_dummy_password(self, password: str) -> None:
        await asyncio.to_thread(self._passwords.verify_dummy, password)

    @staticmethod
    def _audit(
        event_type: str,
        occurred_at: datetime,
        context: AuthContext,
        actor_user_id: UUID,
        target_id: UUID,
        *,
        target_type: str = "user",
    ) -> AuditEvent:
        return AuditEvent(
            event_type=event_type,
            occurred_at=occurred_at,
            actor_user_id=actor_user_id,
            target_type=target_type,
            target_id=target_id,
            request_id=context.request_id,
            ip_hash=context.ip_hash,
        )
