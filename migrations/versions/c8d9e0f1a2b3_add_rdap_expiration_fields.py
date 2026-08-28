"""add RDAP expiration source fields

Revision ID: c8d9e0f1a2b3
Revises: b7c6d5e4f3a2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: str | Sequence[str] | None = "b7c6d5e4f3a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("managed_domain") as batch:
        batch.add_column(sa.Column("registry_expires_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("registrar_expires_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column(
                "expiration_status",
                sa.String(length=32),
                nullable=False,
                server_default="unknown",
            )
        )
        batch.add_column(sa.Column("expiration_checked_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("registrar_rdap_url", sa.String(length=2048)))
        batch.create_check_constraint(
            "managed_domain_expiration_status_valid",
            "expiration_status IN ('active', 'grace_period', 'expired', 'released', 'unknown')",
        )

    # Existing expires_at values were collected from registry RDAP.  They remain
    # useful context but must not be mistaken for registrar expiration dates.
    op.execute(
        "UPDATE managed_domain "
        "SET registry_expires_at = expires_at, expiration_status = 'unknown'"
    )


def downgrade() -> None:
    with op.batch_alter_table("managed_domain") as batch:
        batch.drop_constraint("managed_domain_expiration_status_valid", type_="check")
        batch.drop_column("registrar_rdap_url")
        batch.drop_column("expiration_checked_at")
        batch.drop_column("expiration_status")
        batch.drop_column("registrar_expires_at")
        batch.drop_column("registry_expires_at")
