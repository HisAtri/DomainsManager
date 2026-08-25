from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

from domainsmanager_persistence.db import metadata

JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    metadata = metadata


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SystemState(Base):
    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AppUser(TimestampMixin, Base):
    __tablename__ = "app_user"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'admin')", name="valid_role"),
        CheckConstraint(
            "NOT totp_enabled OR totp_secret_ciphertext IS NOT NULL",
            name="totp_secret_when_enabled",
        ),
        CheckConstraint(
            "banned_at IS NULL OR ban_reason IS NOT NULL",
            name="ban_reason_when_banned",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    username_normalized: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    totp_secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    banned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ban_reason: Mapped[str | None] = mapped_column(String(512))
    banned_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    __tablename__ = "auth_session"
    __table_args__ = (
        Index("ix_auth_session_user_revoked", "user_id", "revoked_at"),
        Index("ix_auth_session_expires_at", "absolute_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(64))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))


class AuthRefreshToken(Base):
    __tablename__ = "auth_refresh_token"
    __table_args__ = (
        Index("ix_auth_refresh_token_session_issued", "session_id", "issued_at"),
        Index("ix_auth_refresh_token_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_session.id", ondelete="CASCADE"), nullable=False
    )
    parent_token_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("auth_refresh_token.id", ondelete="SET NULL"), unique=True
    )
    replaced_by_token_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("auth_refresh_token.id", ondelete="SET NULL")
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ManagedDomain(TimestampMixin, Base):
    __tablename__ = "managed_domain"
    __table_args__ = (
        UniqueConstraint("user_id", "name_ascii"),
        Index("ix_managed_domain_schedule", "monitor_enabled", "next_check_at"),
        Index("ix_managed_domain_expires_at", "expires_at"),
        Index(
            "ix_managed_domain_user_active_name",
            "user_id",
            "deleted_at",
            "name_ascii",
            "id",
        ),
        CheckConstraint("version >= 1", name="managed_domain_version_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    name_ascii: Mapped[str] = mapped_column(String(253), nullable=False)
    name_unicode: Mapped[str] = mapped_column(String(253), nullable=False)
    registrable_domain: Mapped[str] = mapped_column(String(253), nullable=False)
    public_suffix: Mapped[str] = mapped_column(String(253), nullable=False)
    tld: Mapped[str] = mapped_column(String(63), nullable=False)
    registrar_json: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)
    statuses: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registry_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    nameservers: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    dnssec_enabled: Mapped[bool | None] = mapped_column(Boolean)
    latest_source: Mapped[str | None] = mapped_column(String(16))
    last_successful_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_outcome: Mapped[str | None] = mapped_column(String(32))
    monitor_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    renewal_mode: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )


class DomainRefreshTask(TimestampMixin, Base):
    __tablename__ = "domain_refresh_task"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'success', 'info', 'warning', 'failed')",
            name="domain_refresh_task_valid_status",
        ),
        Index(
            "ix_domain_refresh_task_claim",
            "status",
            "available_at",
            "lease_until",
        ),
        Index("ix_domain_refresh_task_domain_created", "managed_domain_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    managed_domain_id: Mapped[UUID] = mapped_column(
        ForeignKey("managed_domain.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    force_refresh: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    domain_check_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("domain_check.id", ondelete="SET NULL")
    )
    source_check_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("domain_check.id", ondelete="SET NULL")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(512))
    result_code: Mapped[str | None] = mapped_column(String(64))
    result_message: Mapped[str | None] = mapped_column(String(512))
    fresh_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_record"
    __table_args__ = (
        UniqueConstraint("user_id", "operation", "resource_id", "key"),
        Index("ix_idempotency_record_expires", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("domain_refresh_task.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GlobalSetting(Base):
    __tablename__ = "global_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LookupRecord(Base):
    __tablename__ = "lookup_record"
    __table_args__ = (
        UniqueConstraint("namespace", "cache_key", "content_hash", "observed_at"),
        CheckConstraint("fresh_until >= observed_at", name="fresh_after_observed"),
        CheckConstraint(
            "stale_until IS NULL OR stale_until >= fresh_until",
            name="stale_after_fresh",
        ),
        Index("ix_lookup_record_key_observed", "namespace", "cache_key", "observed_at"),
        Index("ix_lookup_record_created_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(512), nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    record_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    protocol: Mapped[str | None] = mapped_column(String(16))
    endpoint: Mapped[str | None] = mapped_column(String(2048))
    status_code: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload_codec: Mapped[str] = mapped_column(String(32), nullable=False)
    plaintext_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fresh_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stale_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_usable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    unusable_reason: Mapped[str | None] = mapped_column(String(256))
    encryption_scheme: Mapped[str | None] = mapped_column(String(32))
    encryption_key_id: Mapped[str | None] = mapped_column(String(128))
    encryption_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LookupCacheHead(Base):
    __tablename__ = "lookup_cache_head"

    namespace: Mapped[str] = mapped_column(String(64), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    record_id: Mapped[UUID] = mapped_column(
        ForeignKey("lookup_record.id", ondelete="RESTRICT"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LookupRefreshLease(Base):
    __tablename__ = "lookup_refresh_lease"
    __table_args__ = (Index("ix_lookup_refresh_lease_until", "lease_until"),)

    namespace: Mapped[str] = mapped_column(String(64), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    lease_token: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    lease_owner: Mapped[str] = mapped_column(String(128), nullable=False)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ParsedSnapshot(Base):
    __tablename__ = "parsed_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "lookup_record_id", "parser_key", "parser_version", "schema_version"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    lookup_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("lookup_record.id", ondelete="CASCADE"), nullable=False
    )
    parser_key: Mapped[str] = mapped_column(String(128), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DomainCheck(Base):
    __tablename__ = "domain_check"
    __table_args__ = (
        Index("ix_domain_check_domain_checked", "managed_domain_id", "checked_at"),
        Index("ix_domain_check_outcome_checked", "outcome", "checked_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    managed_domain_id: Mapped[UUID] = mapped_column(
        ForeignKey("managed_domain.id", ondelete="CASCADE"), nullable=False
    )
    lookup_record_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("lookup_record.id", ondelete="SET NULL")
    )
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(512))
    protocol: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str | None] = mapped_column(String(2048))
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    changed_fields: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    parser_key: Mapped[str | None] = mapped_column(String(128))
    parser_version: Mapped[str | None] = mapped_column(String(64))
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NotificationRule(TimestampMixin, Base):
    __tablename__ = "notification_rule"
    __table_args__ = (
        CheckConstraint("event_type IN ('expiration', 'status_change', 'query_failure')", name="notification_rule_valid_event_type"),
        CheckConstraint("channel IN ('email', 'webhook')", name="notification_rule_valid_channel"),
        Index("ix_notification_rule_user_domain", "user_id", "managed_domain_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    managed_domain_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("managed_domain.id", ondelete="CASCADE")
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    days_before: Mapped[int | None] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    channel_config: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationOutbox(TimestampMixin, Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'sent', 'dead_letter')",
            name="notification_outbox_valid_status",
        ),
        CheckConstraint(
            "attempt_count >= 0", name="notification_outbox_attempt_count_nonnegative"
        ),
        Index("ix_notification_outbox_status_available", "status", "available_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    notification_rule_id: Mapped[UUID] = mapped_column(
        ForeignKey("notification_rule.id", ondelete="RESTRICT"), nullable=False
    )
    managed_domain_id: Mapped[UUID] = mapped_column(
        ForeignKey("managed_domain.id", ondelete="CASCADE"), nullable=False
    )
    domain_check_id: Mapped[UUID] = mapped_column(
        ForeignKey("domain_check.id", ondelete="CASCADE"), nullable=False
    )
    deduplication_key: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    last_error: Mapped[str | None] = mapped_column(String(512))


class SecurityAuditEvent(Base):
    __tablename__ = "security_audit_event"
    __table_args__ = (
        Index("ix_security_audit_actor_occurred", "actor_user_id", "occurred_at"),
        Index(
            "ix_security_audit_target_occurred",
            "target_type",
            "target_id",
            "occurred_at",
        ),
        Index("ix_security_audit_event_occurred", "event_type", "occurred_at"),
        Index("ix_security_audit_request_id", "request_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[UUID | None] = mapped_column(Uuid)
    request_id: Mapped[str | None] = mapped_column(String(128))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON_TYPE, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicSuffixRule(TimestampMixin, Base):
    __tablename__ = "public_suffix_rule"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    rule: Mapped[str] = mapped_column(String(253), unique=True, nullable=False)
    rule_type: Mapped[str] = mapped_column(String(16), nullable=False)
    section: Mapped[str] = mapped_column(String(16), nullable=False)
    source_version: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
