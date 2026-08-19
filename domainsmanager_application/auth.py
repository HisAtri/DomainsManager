from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID


class DuplicateRecordError(RuntimeError):
    pass


class ConcurrentUpdateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: UUID
    username: str
    username_normalized: str
    password_hash: str
    email: str | None
    role: str
    preferences: dict[str, Any]
    is_active: bool
    banned_at: datetime | None
    password_changed_at: datetime
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: UUID
    user_id: UUID
    created_at: datetime
    last_seen_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None
    revoke_reason: str | None
    ip_hash: str | None
    user_agent: str | None


@dataclass(frozen=True, slots=True)
class RefreshTokenRecord:
    id: UUID
    session_id: UUID
    parent_token_id: UUID | None
    replaced_by_token_id: UUID | None
    token_hash: bytes
    issued_at: datetime
    expires_at: datetime
    consumed_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_type: str
    occurred_at: datetime
    actor_user_id: UUID | None = None
    target_type: str | None = None
    target_id: UUID | None = None
    request_id: str | None = None
    ip_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class UserRepository(Protocol):
    async def count(self) -> int: ...

    async def get_by_id(
        self,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> UserRecord | None: ...

    async def get_by_username(
        self,
        username_normalized: str,
        *,
        for_update: bool = False,
    ) -> UserRecord | None: ...

    async def add(self, user: UserRecord) -> None: ...

    async def set_last_login(self, user_id: UUID, at: datetime) -> None: ...

    async def update_profile(
        self,
        user_id: UUID,
        *,
        email: str | None,
        updated_at: datetime,
    ) -> None: ...

    async def update_preferences(
        self,
        user_id: UUID,
        preferences: dict[str, Any],
        updated_at: datetime,
    ) -> None: ...

    async def update_password(
        self,
        user_id: UUID,
        password_hash: str,
        changed_at: datetime,
    ) -> None: ...

    async def set_account_state(
        self,
        user_id: UUID,
        *,
        is_active: bool,
        banned_at: datetime | None,
        ban_reason: str | None,
        banned_by_user_id: UUID | None,
        updated_at: datetime,
    ) -> None: ...


class AuthSessionRepository(Protocol):
    async def add_session(self, session: SessionRecord) -> None: ...

    async def add_token(self, token: RefreshTokenRecord) -> None: ...

    async def get_session(
        self,
        session_id: UUID,
        *,
        for_update: bool = False,
    ) -> SessionRecord | None: ...

    async def get_token(
        self,
        token_id: UUID,
        *,
        for_update: bool = False,
    ) -> RefreshTokenRecord | None: ...

    async def consume_token(
        self,
        token_id: UUID,
        replacement_id: UUID,
        consumed_at: datetime,
    ) -> None: ...

    async def touch_session(self, session_id: UUID, at: datetime) -> None: ...

    async def revoke_session(
        self,
        session_id: UUID,
        at: datetime,
        reason: str,
    ) -> None: ...

    async def revoke_other_sessions(
        self,
        user_id: UUID,
        current_session_id: UUID,
        at: datetime,
        reason: str,
    ) -> None: ...

    async def revoke_all_sessions(
        self,
        user_id: UUID,
        at: datetime,
        reason: str,
    ) -> None: ...


class AuditRepository(Protocol):
    async def add(self, event: AuditEvent) -> None: ...


class UnitOfWork(Protocol):
    users: UserRepository
    sessions: AuthSessionRepository
    audits: AuditRepository

    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...
