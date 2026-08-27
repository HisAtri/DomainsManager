"""version global settings

Revision ID: f2a3b4c5d6e7
Revises: c4e8f2a6b913
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "c4e8f2a6b913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "global_setting",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "global_setting", sa.Column("updated_by_user_id", sa.Uuid(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("global_setting", "updated_by_user_id")
    op.drop_column("global_setting", "version")
