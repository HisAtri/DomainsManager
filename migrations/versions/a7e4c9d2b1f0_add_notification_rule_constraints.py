"""add notification rule constraints

Revision ID: a7e4c9d2b1f0
Revises: f1a2b3c4d5e6
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a7e4c9d2b1f0"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("notification_rule") as batch:
        batch.create_check_constraint(
            "notification_rule_valid_event_type",
            "event_type IN ('expiration', 'status_change', 'query_failure')",
        )
        batch.create_check_constraint(
            "notification_rule_valid_channel", "channel IN ('email', 'webhook')"
        )
        batch.create_index("ix_notification_rule_user_domain", ["user_id", "managed_domain_id"])


def downgrade() -> None:
    with op.batch_alter_table("notification_rule") as batch:
        batch.drop_index("ix_notification_rule_user_domain")
        batch.drop_constraint("notification_rule_valid_channel", type_="check")
        batch.drop_constraint("notification_rule_valid_event_type", type_="check")
