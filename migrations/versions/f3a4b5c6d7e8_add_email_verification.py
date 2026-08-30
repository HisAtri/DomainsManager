"""add email verification state

Revision ID: f3a4b5c6d7e8
Revises: e0f1a2b3c4d5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: str | Sequence[str] | None = "e0f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("app_user") as batch:
        batch.add_column(sa.Column("pending_email", sa.String(length=320)))
        batch.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True)))
    op.create_table(
        "email_verification_challenge",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.String(length=64), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_email_verification_challenge_user", "email_verification_challenge", ["user_id", "created_at"])
    op.create_index("ix_email_verification_challenge_expires", "email_verification_challenge", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_email_verification_challenge_expires", table_name="email_verification_challenge")
    op.drop_index("ix_email_verification_challenge_user", table_name="email_verification_challenge")
    op.drop_table("email_verification_challenge")
    with op.batch_alter_table("app_user") as batch:
        batch.drop_column("email_verified_at")
        batch.drop_column("pending_email")
