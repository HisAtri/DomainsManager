"""Name the stored PKCE verifier by its actual plaintext representation."""

from alembic import op

revision = "6b7c8d9e0f12"
down_revision = "f4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "oauth_authorization_attempt",
        "code_verifier_ciphertext",
        new_column_name="code_verifier",
    )


def downgrade() -> None:
    op.alter_column(
        "oauth_authorization_attempt",
        "code_verifier",
        new_column_name="code_verifier_ciphertext",
    )
