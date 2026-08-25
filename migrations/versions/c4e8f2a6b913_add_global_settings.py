"""add global settings

Revision ID: c4e8f2a6b913
Revises: b5c9d1e3f702
Create Date: 2026-08-25 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8f2a6b913"
down_revision: str | Sequence[str] | None = "b5c9d1e3f702"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "global_setting",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=256), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_global_setting")),
    )


def downgrade() -> None:
    op.drop_table("global_setting")
