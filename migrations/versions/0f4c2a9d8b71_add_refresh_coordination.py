"""add refresh task and endpoint coordination

Revision ID: 0f4c2a9d8b71
Revises: f3a4b5c6d7e8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0f4c2a9d8b71"
down_revision: str | Sequence[str] | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("domain_refresh_task") as batch:
        batch.add_column(
            sa.Column(
                "origin",
                sa.String(length=24),
                nullable=False,
                server_default="manual",
            )
        )
        batch.create_check_constraint(
            "ck_domain_refresh_task_domain_refresh_task_valid_origin",
            "origin IN ('manual', 'scheduled', 'monitor_enabled', 'backfill')",
        )

    op.execute(
        """
        UPDATE domain_refresh_task
        SET origin = CASE
            WHEN EXISTS (
                SELECT 1 FROM idempotency_record i
                WHERE i.task_id = domain_refresh_task.id
                  AND i.key LIKE 'schedule:%'
            ) THEN 'scheduled'
            WHEN EXISTS (
                SELECT 1 FROM idempotency_record i
                WHERE i.task_id = domain_refresh_task.id
                  AND i.key LIKE 'monitor-enabled:%'
            ) THEN 'monitor_enabled'
            WHEN EXISTS (
                SELECT 1 FROM idempotency_record i
                WHERE i.task_id = domain_refresh_task.id
                  AND i.key = 'rdap-expiration-backfill-v1'
            ) THEN 'backfill'
            ELSE 'manual'
        END
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY managed_domain_id
                       ORDER BY CASE WHEN status = 'running' THEN 0 ELSE 1 END,
                                created_at,
                                id
                   ) AS position
            FROM domain_refresh_task
            WHERE status IN ('queued', 'running')
        )
        UPDATE domain_refresh_task
        SET status = 'failed',
            error_code = 'duplicate_merged',
            error_message = 'Duplicate active refresh task merged during migration',
            result_code = 'failed',
            result_message = 'Duplicate active refresh task merged during migration',
            completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
            lease_token = NULL,
            lease_owner = NULL,
            lease_until = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id IN (SELECT id FROM ranked WHERE position > 1)
        """
    )
    op.create_index(
        "uq_domain_refresh_task_active_domain",
        "domain_refresh_task",
        ["managed_domain_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
        sqlite_where=sa.text("status IN ('queued', 'running')"),
    )

    op.create_table(
        "endpoint_request_gate",
        sa.Column("protocol", sa.String(length=16), nullable=False),
        sa.Column("endpoint", sa.String(length=2048), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "failure_count >= 0",
            name=op.f("ck_endpoint_request_gate_endpoint_gate_failure_nonnegative"),
        ),
        sa.PrimaryKeyConstraint(
            "protocol", "endpoint", name=op.f("pk_endpoint_request_gate")
        ),
    )
    op.create_index(
        "ix_endpoint_request_gate_blocked",
        "endpoint_request_gate",
        ["blocked_until"],
    )
    op.create_index(
        "ix_endpoint_request_gate_lease",
        "endpoint_request_gate",
        ["lease_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_endpoint_request_gate_lease", table_name="endpoint_request_gate")
    op.drop_index(
        "ix_endpoint_request_gate_blocked", table_name="endpoint_request_gate"
    )
    op.drop_table("endpoint_request_gate")
    op.drop_index(
        "uq_domain_refresh_task_active_domain", table_name="domain_refresh_task"
    )
    with op.batch_alter_table("domain_refresh_task") as batch:
        batch.drop_constraint(
            "ck_domain_refresh_task_domain_refresh_task_valid_origin",
            type_="check",
        )
        batch.drop_column("origin")
