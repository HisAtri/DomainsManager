"""Persist the one-time username setup for newly created OAuth users."""

import sqlalchemy as sa
from alembic import op

revision = "7c8d9e0f1234"
down_revision = "6b7c8d9e0f12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 既有账号保持原有用户名；只有新建的第三方账号进入引导。
    op.add_column(
        "app_user",
        sa.Column(
            "username_setup_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("app_user", "username_setup_required")
