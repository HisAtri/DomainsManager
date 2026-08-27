"""expand global setting value storage

Revision ID: a9e8f7d6c5b4
Revises: f2a3b4c5d6e7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9e8f7d6c5b4"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("global_setting") as batch:
        batch.alter_column("value", existing_type=sa.String(length=256), type_=sa.Text())


def downgrade() -> None:
    with op.batch_alter_table("global_setting") as batch:
        batch.alter_column("value", existing_type=sa.Text(), type_=sa.String(length=256))
