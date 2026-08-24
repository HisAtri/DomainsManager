"""add notification outbox constraints

Revision ID: d6f4a9b8c2e1
Revises: c9d2e4f7a1b3
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d6f4a9b8c2e1"
down_revision: str | Sequence[str] | None = "c9d2e4f7a1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("notification_outbox") as batch:
        batch.create_check_constraint(
            "notification_outbox_valid_status",
            "status IN ('pending', 'running', 'sent', 'dead_letter')",
        )
        batch.create_check_constraint(
            "notification_outbox_attempt_count_nonnegative", "attempt_count >= 0"
        )


def downgrade() -> None:
    with op.batch_alter_table("notification_outbox") as batch:
        batch.drop_constraint("notification_outbox_attempt_count_nonnegative", type_="check")
        batch.drop_constraint("notification_outbox_valid_status", type_="check")
