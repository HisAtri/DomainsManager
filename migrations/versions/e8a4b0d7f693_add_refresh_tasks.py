"""add refresh tasks

Revision ID: e8a4b0d7f693
Revises: d4e7f1c9a862
Create Date: 2026-08-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8a4b0d7f693"
down_revision: str | Sequence[str] | None = "d4e7f1c9a862"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "domain_refresh_task",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("managed_domain_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("force_refresh", sa.Boolean(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("domain_check_id", sa.Uuid(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_domain_refresh_task_domain_refresh_task_valid_status"),
        ),
        sa.ForeignKeyConstraint(
            ["domain_check_id"], ["domain_check.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["managed_domain_id"], ["managed_domain.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_domain_refresh_task")),
    )
    op.create_index(
        "ix_domain_refresh_task_claim",
        "domain_refresh_task",
        ["status", "available_at", "lease_until"],
    )
    op.create_index(
        "ix_domain_refresh_task_domain_created",
        "domain_refresh_task",
        ["managed_domain_id", "created_at"],
    )
    op.create_table(
        "idempotency_record",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["domain_refresh_task.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_record")),
        sa.UniqueConstraint(
            "user_id",
            "operation",
            "resource_id",
            "key",
            name=op.f("uq_idempotency_record_user_id"),
        ),
    )
    op.create_index(
        "ix_idempotency_record_expires", "idempotency_record", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_idempotency_record_expires", table_name="idempotency_record")
    op.drop_table("idempotency_record")
    op.drop_index(
        "ix_domain_refresh_task_domain_created", table_name="domain_refresh_task"
    )
    op.drop_index("ix_domain_refresh_task_claim", table_name="domain_refresh_task")
    op.drop_table("domain_refresh_task")
