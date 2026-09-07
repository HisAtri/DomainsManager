"""optimize administrator domain indexes

Revision ID: 3a6f9d2c7b10
Revises: 21c5b0e8d9a4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3a6f9d2c7b10"
down_revision: str | Sequence[str] | None = "21c5b0e8d9a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_managed_domain_admin_owner_created", table_name="managed_domain")
    op.drop_index("ix_managed_domain_admin_list", table_name="managed_domain")
    op.create_index(
        "ix_managed_domain_admin_list",
        "managed_domain",
        ["created_at", "id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_managed_domain_admin_owner_created",
        "managed_domain",
        ["user_id", "created_at", "id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_managed_domain_admin_owner_created", table_name="managed_domain")
    op.drop_index("ix_managed_domain_admin_list", table_name="managed_domain")
    op.create_index(
        "ix_managed_domain_admin_list",
        "managed_domain",
        ["deleted_at", "created_at", "id"],
    )
    op.create_index(
        "ix_managed_domain_admin_owner_created",
        "managed_domain",
        ["user_id", "deleted_at", "created_at", "id"],
    )
