from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Protocol
from uuid import UUID, uuid4

from domainsmanager_application.auth import AuditEvent, DuplicateRecordError, UserRecord
from domainsmanager_application.security import normalize_username, utc_now
from domainsmanager_application.services import (
    AuthContext,
    AuthenticatedUser,
    AuthenticationResult,
    AuthService,
    RegistrationDisabledError,
    UsernameTakenError,
)


class OAuthError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code, self.status_code = code, status_code


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    provider_key: str
    subject: str
    username: str | None
    display_name: str | None
    avatar_url: str | None
    active: bool
    profile: dict[str, Any]


@dataclass(frozen=True, slots=True)
class IdentityRecord:
    id: UUID
    user_id: UUID
    provider_key: str
    provider_subject: str
    provider_username: str | None
    display_name: str | None
    avatar_url: str | None
    profile_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None


@dataclass(frozen=True, slots=True)
class AuthorizationAttempt:
    id: UUID
    provider_key: str
    intent: str
    state_hash: str
    browser_hash: str
    user_id: UUID | None
    session_id: UUID | None
    return_to: str
    code_verifier: bytes | None
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None


class OAuthRepository(Protocol):
    async def add_attempt(self, attempt: AuthorizationAttempt) -> None: ...
    async def consume_attempt(
        self, provider: str, state_hash: str, browser_hash: str, now: datetime
    ) -> AuthorizationAttempt | None: ...
    async def cleanup(self, now: datetime) -> None: ...
    async def get_identity(
        self, provider: str, subject: str
    ) -> IdentityRecord | None: ...
    async def list_identities(self, user_id: UUID) -> list[IdentityRecord]: ...
    async def add_identity(self, identity: IdentityRecord) -> None: ...
    async def update_identity(self, identity: IdentityRecord) -> None: ...
    async def delete_identity(self, identity_id: UUID) -> None: ...
    async def lock_user(self, user_id: UUID) -> None: ...
    async def enable_password(self, user_id: UUID) -> None: ...


class OAuthProvider(Protocol):
    key: str
    display_name: str
    pkce: bool

    def authorization_url(
        self, state: str, redirect_uri: str, code_challenge: str | None
    ) -> str: ...
    async def fetch_identity(
        self, code: str, redirect_uri: str, code_verifier: str | None
    ) -> ExternalIdentity: ...


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class OAuthService:
    def __init__(
        self,
        unit_of_work,
        auth: AuthService,
        providers: dict[str, OAuthProvider],
        base_url: str | None,
        api_prefix: str,
        ttl_seconds: int = 600,
        clock=utc_now,
        configuration_loader=None,
    ):
        self.uow, self.auth, self.providers = unit_of_work, auth, providers
        self.base_url = (base_url or "").rstrip("/")
        self.api_prefix, self.ttl, self.clock = api_prefix, ttl_seconds, clock
        self.configuration_loader = configuration_loader

    async def load_configuration(self) -> None:
        if self.configuration_loader is not None:
            self.providers, self.base_url, self.ttl = await self.configuration_loader()

    def provider(self, key: str) -> OAuthProvider:
        if key not in self.providers:
            raise OAuthError(
                "oauth_provider_not_found", "OAuth provider is not configured", 404
            )
        return self.providers[key]

    def callback_url(self, provider: str) -> str:
        return f"{self.base_url}{self.api_prefix}/auth/oauth2/{provider}/callback"

    def require_recent(self, current: AuthenticatedUser) -> None:
        if self.clock() - current.session.created_at > timedelta(minutes=10):
            raise OAuthError(
                "oauth_reauthentication_required",
                "Please sign in again before changing login methods",
                403,
            )

    async def begin(
        self, key: str, current: AuthenticatedUser | None = None
    ) -> tuple[str, str, str]:
        await self.load_configuration()
        provider = self.provider(key)
        if not self.base_url:
            raise OAuthError("oauth_config_invalid", "Site URL is not configured", 503)
        if current:
            self.require_recent(current)
        state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(32) if provider.pkce else None
        challenge = (
            base64.urlsafe_b64encode(sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
            if verifier
            else None
        )
        now = self.clock()
        attempt = AuthorizationAttempt(
            uuid4(),
            key,
            "link" if current else "login",
            digest(state),
            digest(browser),
            current.user.id if current else None,
            current.session.id if current else None,
            "settings" if current else "dashboard",
            verifier.encode() if verifier else None,
            now,
            now + timedelta(seconds=self.ttl),
        )
        async with self.uow() as uow:
            await uow.oauth.cleanup(now)
            await uow.oauth.add_attempt(attempt)
            await uow.commit()
        return (
            provider.authorization_url(state, self.callback_url(key), challenge),
            state,
            browser,
        )

    async def complete(
        self,
        key: str,
        state: str,
        browser: str,
        code: str | None,
        error: str | None,
        refresh_cookie: str | None,
        context: AuthContext,
    ) -> tuple[AuthenticationResult | None, str]:
        await self.load_configuration()
        provider = self.provider(key)
        async with self.uow() as uow:
            # 原子消费并在联网前提交：并发回调只允许一次交换授权码，网络期间不持有数据库锁。
            attempt = await uow.oauth.consume_attempt(
                key, digest(state), digest(browser), self.clock()
            )
            await uow.commit()
        if attempt is None:
            raise OAuthError(
                "oauth_invalid_state", "Authorization expired or browser did not match"
            )
        if error or not code:
            raise OAuthError(
                "oauth_access_denied", "Authorization was cancelled or code is missing"
            )
        if attempt.intent == "link":
            await self.auth.validate_refresh_session(
                refresh_cookie or "", attempt.session_id
            )
        verifier = attempt.code_verifier.decode() if attempt.code_verifier else None
        external = await provider.fetch_identity(code, self.callback_url(key), verifier)
        if (
            external.provider_key != key
            or not external.subject
            or len(external.subject) > 255
        ):
            raise OAuthError(
                "oauth_provider_error", "Provider returned an invalid identity", 502
            )
        if not external.active:
            raise OAuthError(
                "oauth_inactive_identity", "External account is inactive", 403
            )
        # 唯一约束竞争后重新读取获胜身份，不遗留没有身份的空账号。
        for retry in range(2):
            try:
                result = await self._finish(attempt, external, context)
                target = (
                    "onboarding"
                    if result and result.user.username_setup_required
                    else attempt.return_to
                )
                return result, target
            except DuplicateRecordError:
                if retry:
                    raise OAuthError(
                        "oauth_identity_in_use", "Identity is already linked", 409
                    ) from None
        raise AssertionError("unreachable")

    async def _finish(
        self,
        attempt: AuthorizationAttempt,
        external: ExternalIdentity,
        context: AuthContext,
    ) -> AuthenticationResult | None:
        now = self.clock()
        async with self.uow() as uow:
            stored = await uow.oauth.get_identity(
                external.provider_key, external.subject
            )
            if attempt.intent == "link":
                await uow.oauth.lock_user(attempt.user_id)
                user = await uow.users.get_by_id(attempt.user_id)
                session = await uow.sessions.get_session(attempt.session_id)
                if (
                    user is None
                    or session is None
                    or session.user_id != user.id
                    or session.revoked_at
                    or session.absolute_expires_at <= now
                ):
                    raise OAuthError(
                        "oauth_session_changed",
                        "The linking session is no longer active",
                        403,
                    )
                self.auth._ensure_user_active(user)
                stored = await uow.oauth.get_identity(
                    external.provider_key, external.subject
                )
                if stored and stored.user_id != user.id:
                    raise OAuthError(
                        "oauth_identity_in_use", "Identity is already linked", 409
                    )
                identities = await uow.oauth.list_identities(user.id)
                if any(
                    item.provider_key == external.provider_key
                    and item.provider_subject != external.subject
                    for item in identities
                ):
                    raise OAuthError(
                        "oauth_provider_already_linked",
                        "A different account from this provider is linked",
                        409,
                    )
            elif stored:
                await uow.oauth.lock_user(stored.user_id)
                user = await uow.users.get_by_id(stored.user_id)
                # 身份可能在读取后被解绑，必须在用户锁内重查。
                stored = await uow.oauth.get_identity(
                    external.provider_key, external.subject
                )
                if user is None or stored is None or stored.user_id != user.id:
                    raise OAuthError(
                        "oauth_identity_changed", "Identity changed; sign in again", 409
                    )
                self.auth._ensure_user_active(user)
            else:
                if not self.auth._configuration.registration_enabled:
                    raise RegistrationDisabledError("registration is disabled")
                user_id = uuid4()
                # 外部用户名可更名或与本地用户名重复，仅作为展示资料。
                base = f"{external.provider_key}-{external.subject}"
                username = (
                    base
                    if len(base) <= 128
                    and all(c.isascii() and (c.isalnum() or c in "_.-") for c in base)
                    else f"oauth-{user_id.hex}"
                )
                if await uow.users.get_by_username(username.casefold()):
                    username = f"oauth-{user_id.hex}"
                user = UserRecord(
                    user_id,
                    username,
                    username.casefold(),
                    await self.auth._hash_password(secrets.token_urlsafe(48)),
                    None,
                    "user",
                    {},
                    True,
                    None,
                    now,
                    None,
                    now,
                    now,
                    password_auth_enabled=False,
                    username_setup_required=True,
                )
                await uow.users.add(user)
            identity = IdentityRecord(
                stored.id if stored else uuid4(),
                user.id,
                external.provider_key,
                external.subject,
                external.username,
                external.display_name,
                external.avatar_url,
                external.profile,
                stored.created_at if stored else now,
                now,
                now
                if attempt.intent == "login"
                else stored.last_login_at
                if stored
                else None,
            )
            if stored:
                await uow.oauth.update_identity(identity)
            else:
                await uow.oauth.add_identity(identity)
            result = None
            if attempt.intent == "login":
                await uow.users.set_last_login(user.id, now)
                result = await self.auth._create_session(
                    uow, replace(user, last_login_at=now, updated_at=now), context, now
                )
            await uow.audits.add(
                AuditEvent(
                    "oauth.linked" if attempt.intent == "link" else "oauth.login",
                    now,
                    user.id,
                    "user",
                    user.id,
                    context.request_id,
                    context.ip_hash,
                    {"provider": external.provider_key},
                )
            )
            await uow.commit()
            return result

    async def accounts(self, user_id: UUID) -> list[IdentityRecord]:
        async with self.uow() as uow:
            return await uow.oauth.list_identities(user_id)

    async def set_username(
        self, current: AuthenticatedUser, username: str, context: AuthContext
    ) -> UserRecord:
        display, normalized = normalize_username(username)
        try:
            async with self.uow() as uow:
                # 和登录方式修改共用用户锁，使并发提交也只能完成一次引导。
                await uow.oauth.lock_user(current.user.id)
                user = await self._validate_mutation(uow, current)
                if not user.username_setup_required:
                    raise OAuthError(
                        "username_already_set",
                        "Username setup is already complete",
                        409,
                    )
                existing = await uow.users.get_by_username(normalized)
                if existing and existing.id != user.id:
                    raise UsernameTakenError("username is already in use")
                now = self.clock()
                await uow.users.complete_username_setup(
                    user.id, display, normalized, now
                )
                await uow.audits.add(
                    AuditEvent(
                        "user.username_set",
                        now,
                        user.id,
                        "user",
                        user.id,
                        context.request_id,
                    )
                )
                await uow.commit()
                return replace(
                    user,
                    username=display,
                    username_normalized=normalized,
                    username_setup_required=False,
                    updated_at=now,
                )
        except DuplicateRecordError as error:
            raise UsernameTakenError("username is already in use") from error

    async def _validate_mutation(self, uow, current: AuthenticatedUser) -> UserRecord:
        user = await uow.users.get_by_id(current.user.id)
        session = await uow.sessions.get_session(current.session.id)
        if (
            user is None
            or session is None
            or session.user_id != current.user.id
            or session.revoked_at is not None
            or session.absolute_expires_at <= self.clock()
            or user.password_changed_at != current.user.password_changed_at
        ):
            raise OAuthError(
                "oauth_session_changed", "Session changed; sign in again", 403
            )
        self.auth._ensure_user_active(user)
        return user

    async def unlink(
        self, key: str, current: AuthenticatedUser, context: AuthContext
    ) -> None:
        self.require_recent(current)
        async with self.uow() as uow:
            await uow.oauth.lock_user(current.user.id)
            user = await self._validate_mutation(uow, current)
            identities = await uow.oauth.list_identities(user.id)
            target = next(
                (item for item in identities if item.provider_key == key), None
            )
            if target is None:
                raise OAuthError(
                    "oauth_identity_not_found", "Identity is not linked", 404
                )
            if not user.password_auth_enabled and not any(
                item.provider_key != key and item.provider_key in self.providers
                for item in identities
            ):
                raise OAuthError(
                    "oauth_last_login_method",
                    "Set a password or link another provider first",
                    409,
                )
            await uow.oauth.delete_identity(target.id)
            # 撤销其他会话，避免解绑后旧的第三方登录会话继续访问。
            await uow.sessions.revoke_other_sessions(
                user.id, current.session.id, self.clock(), "oauth_unlinked"
            )
            await uow.audits.add(
                AuditEvent(
                    "oauth.unlinked",
                    self.clock(),
                    user.id,
                    "user",
                    user.id,
                    context.request_id,
                    context.ip_hash,
                    {"provider": key},
                )
            )
            await uow.commit()

    async def set_password(
        self, current: AuthenticatedUser, password: str, context: AuthContext
    ) -> None:
        self.require_recent(current)
        password_hash = await self.auth._hash_password(password)
        async with self.uow() as uow:
            await uow.oauth.lock_user(current.user.id)
            user = await self._validate_mutation(uow, current)
            if user.password_auth_enabled:
                raise OAuthError(
                    "oauth_password_already_set",
                    "Use the change-password endpoint",
                    409,
                )
            now = max(
                self.clock(), user.password_changed_at + timedelta(microseconds=1)
            )
            await uow.users.update_password(user.id, password_hash, now)
            await uow.oauth.enable_password(user.id)
            await uow.sessions.revoke_other_sessions(
                user.id, current.session.id, now, "password_set"
            )
            await uow.audits.add(
                AuditEvent(
                    "user.password_set",
                    now,
                    user.id,
                    "user",
                    user.id,
                    context.request_id,
                )
            )
            await uow.commit()
