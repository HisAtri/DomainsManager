"""add refresh task results

Revision ID: a2b7d4e8f901
Revises: f1a2b3c4d5e6
Create Date: 2026-08-25 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2b7d4e8f901"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE domain_refresh_task SET status = 'success' WHERE status = 'succeeded'")
    with op.batch_alter_table("domain_refresh_task") as batch:
        batch.drop_constraint(
            op.f("ck_domain_refresh_task_domain_refresh_task_valid_status"),
            type_="check",
        )
        batch.create_check_constraint(
            op.f("ck_domain_refresh_task_domain_refresh_task_valid_status"),
            "status IN ('queued', 'running', 'success', 'info', 'warning', 'failed')",
        )
        batch.add_column(sa.Column("source_check_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("result_code", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("result_message", sa.String(length=512), nullable=True))
        batch.add_column(sa.Column("fresh_until", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_domain_refresh_task_source_check_id_domain_check",
            "domain_check", ["source_check_id"], ["id"], ondelete="SET NULL"
        )
    op.create_index(
        "ix_domain_refresh_task_user_created", "domain_refresh_task", ["user_id", "created_at", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_domain_refresh_task_user_created", table_name="domain_refresh_task")
    with op.batch_alter_table("domain_refresh_task") as batch:
        batch.drop_constraint(
            op.f("fk_domain_refresh_task_source_check_id_domain_check"),
            type_="foreignkey",
        )
        batch.drop_column("fresh_until")
        batch.drop_column("result_message")
        batch.drop_column("result_code")
        batch.drop_column("source_check_id")
        batch.drop_constraint(
            op.f("ck_domain_refresh_task_domain_refresh_task_valid_status"),
            type_="check",
        )
        batch.create_check_constraint(
            op.f("ck_domain_refresh_task_domain_refresh_task_valid_status"),
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
        )
