"""add managed domain soft delete

Revision ID: d4e7f1c9a862
Revises: b3c8172a6d1e
Create Date: 2026-08-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e7f1c9a862"
down_revision: str | Sequence[str] | None = "b3c8172a6d1e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("managed_domain") as batch_op:
        batch_op.add_column(
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("deleted_by_user_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_managed_domain_deleted_by_user_id_app_user"),
            "app_user",
            ["deleted_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_managed_domain_user_active_name",
            ["user_id", "deleted_at", "name_ascii", "id"],
        )
        batch_op.create_check_constraint(
            "managed_domain_version_positive", "version >= 1"
        )


def downgrade() -> None:
    with op.batch_alter_table("managed_domain") as batch_op:
        batch_op.drop_constraint("managed_domain_version_positive", type_="check")
        batch_op.drop_index("ix_managed_domain_user_active_name")
        batch_op.drop_constraint(
            batch_op.f("fk_managed_domain_deleted_by_user_id_app_user"),
            type_="foreignkey",
        )
        batch_op.drop_column("deleted_by_user_id")
        batch_op.drop_column("deleted_at")
