"""add skipped notification status

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d9e0f1a2b3c4"
down_revision: str | Sequence[str] | None = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("notification_outbox") as batch:
        batch.drop_constraint("notification_outbox_valid_status", type_="check")
        batch.create_check_constraint(
            "notification_outbox_valid_status",
            "status IN ('pending', 'running', 'sent', 'dead_letter', 'skipped')",
        )


def downgrade() -> None:
    with op.batch_alter_table("notification_outbox") as batch:
        batch.drop_constraint("notification_outbox_valid_status", type_="check")
        batch.create_check_constraint(
            "notification_outbox_valid_status",
            "status IN ('pending', 'running', 'sent', 'dead_letter')",
        )
