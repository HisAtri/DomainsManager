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


class AppUser(TimestampMixin, Base):
    __tablename__ = "app_user"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    totp_secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ManagedDomain(TimestampMixin, Base):
    __tablename__ = "managed_domain"
    __table_args__ = (
        UniqueConstraint("user_id", "name_ascii"),
        Index("ix_managed_domain_schedule", "monitor_enabled", "next_check_at"),
        Index("ix_managed_domain_expires_at", "expires_at"),
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


class NotificationOutbox(TimestampMixin, Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (Index("ix_notification_outbox_status_available", "status", "available_at"),)

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
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    last_error: Mapped[str | None] = mapped_column(String(512))


class SecurityAuditEvent(Base):
    __tablename__ = "security_audit_event"

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
