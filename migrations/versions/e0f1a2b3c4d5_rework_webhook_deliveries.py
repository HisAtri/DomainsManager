"""rework webhook deliveries

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e0f1a2b3c4d5"
down_revision: str | Sequence[str] | None = "d9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("notification_rule") as batch:
        batch.add_column(sa.Column("webhook_name", sa.String(length=128)))
        batch.drop_constraint("notification_rule_valid_event_type", type_="check")
        batch.create_check_constraint(
            "notification_rule_valid_event_type",
            "event_type IN ('domain.expiration_warning', 'domain.status_changed', 'domain.query_failed')",
        )
        batch.create_check_constraint(
            "notification_rule_valid_webhook_name",
            "(channel = 'webhook' AND webhook_name IS NOT NULL AND length(trim(webhook_name)) BETWEEN 1 AND 128) OR (channel = 'email' AND webhook_name IS NULL)",
        )
    with op.batch_alter_table("notification_outbox") as batch:
        batch.add_column(sa.Column("outcome", sa.String(length=32)))
        batch.add_column(sa.Column("response_status_code", sa.Integer()))
        batch.create_check_constraint(
            "notification_outbox_valid_outcome",
            "outcome IS NULL OR outcome IN ('success', 'redirect_rejected', 'rate_limited', 'http_error', 'network_error', 'tls_error', 'proxy_error', 'configuration_error', 'suppressed')",
        )
        batch.create_check_constraint(
            "notification_outbox_valid_response_status",
            "response_status_code IS NULL OR response_status_code BETWEEN 100 AND 599",
        )


def downgrade() -> None:
    with op.batch_alter_table("notification_outbox") as batch:
        batch.drop_constraint("notification_outbox_valid_response_status", type_="check")
        batch.drop_constraint("notification_outbox_valid_outcome", type_="check")
        batch.drop_column("response_status_code")
        batch.drop_column("outcome")
    with op.batch_alter_table("notification_rule") as batch:
        batch.drop_constraint("notification_rule_valid_webhook_name", type_="check")
        batch.drop_constraint("notification_rule_valid_event_type", type_="check")
        batch.create_check_constraint(
            "notification_rule_valid_event_type",
            "event_type IN ('expiration', 'status_change', 'query_failure')",
        )
        batch.drop_column("webhook_name")
