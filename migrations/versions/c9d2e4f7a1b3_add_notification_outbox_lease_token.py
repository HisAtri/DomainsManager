"""add notification outbox lease token"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d2e4f7a1b3"
down_revision: str | Sequence[str] | None = "a7e4c9d2b1f0"
branch_labels = None
depends_on = None
def upgrade() -> None:
    with op.batch_alter_table("notification_outbox") as batch:
        batch.add_column(sa.Column("lease_token", sa.Uuid(), nullable=True))
def downgrade() -> None:
    with op.batch_alter_table("notification_outbox") as batch:
        batch.drop_column("lease_token")
