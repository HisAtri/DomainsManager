"""add administrator query indexes

Revision ID: b7c6d5e4f3a2
Revises: a9e8f7d6c5b4
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7c6d5e4f3a2"
down_revision: str | Sequence[str] | None = "a9e8f7d6c5b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
    op.create_index(
        "ix_domain_check_admin_checked",
        "domain_check",
        ["checked_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_domain_check_admin_checked", table_name="domain_check")
    op.drop_index("ix_managed_domain_admin_owner_created", table_name="managed_domain")
    op.drop_index("ix_managed_domain_admin_list", table_name="managed_domain")
