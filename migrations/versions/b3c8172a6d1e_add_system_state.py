"""add system state

Revision ID: b3c8172a6d1e
Revises: 2d45f5ae288f
Create Date: 2026-08-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b3c8172a6d1e"
down_revision: Union[str, Sequence[str], None] = "2d45f5ae288f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_state",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_system_state")),
    )


def downgrade() -> None:
    op.drop_table("system_state")
