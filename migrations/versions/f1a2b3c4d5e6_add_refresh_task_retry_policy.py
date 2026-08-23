"""add refresh task retry policy

Revision ID: f1a2b3c4d5e6
Revises: e8a4b0d7f693
Create Date: 2026-08-24 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e8a4b0d7f693"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("domain_refresh_task") as batch:
        batch.add_column(
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5")
        )
        batch.create_check_constraint(
            "ck_domain_refresh_task_max_attempts_positive", "max_attempts >= 1"
        )


def downgrade() -> None:
    with op.batch_alter_table("domain_refresh_task") as batch:
        batch.drop_constraint("ck_domain_refresh_task_max_attempts_positive", type_="check")
        batch.drop_column("max_attempts")
