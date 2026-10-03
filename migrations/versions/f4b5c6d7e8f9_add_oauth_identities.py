"""Add provider-neutral OAuth identities and one-time authorization attempts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f4b5c6d7e8f9"
down_revision = "3a6f9d2c7b10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column(
            "password_auth_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_table(
        "user_auth_identity",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("provider_subject", sa.String(255), nullable=False),
        sa.Column("provider_username", sa.String(255)),
        sa.Column("display_name", sa.String(255)),
        sa.Column("avatar_url", sa.String(2048)),
        sa.Column(
            "profile_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("provider_key", "provider_subject"),
        sa.UniqueConstraint("user_id", "provider_key"),
    )
    op.create_table(
        "oauth_authorization_attempt",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("intent", sa.String(16), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("app_user.id", ondelete="CASCADE")
        ),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("auth_session.id", ondelete="CASCADE"),
        ),
        sa.Column("return_to", sa.String(128), nullable=False),
        sa.Column("code_verifier_ciphertext", sa.LargeBinary()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "intent IN ('login', 'link')",
            name=op.f("ck_oauth_authorization_attempt_oauth_intent"),
        ),
        sa.CheckConstraint(
            "(intent = 'login' AND user_id IS NULL AND session_id IS NULL) OR (intent = 'link' AND user_id IS NOT NULL AND session_id IS NOT NULL)",
            name=op.f("ck_oauth_authorization_attempt_oauth_link_owner"),
        ),
    )
    op.create_index(
        "ix_oauth_attempt_expires", "oauth_authorization_attempt", ["expires_at"]
    )


def downgrade() -> None:
    # 回滚旧版本会移除第三方登录能力；保留用户与其业务数据。
    op.drop_table("oauth_authorization_attempt")
    op.drop_table("user_auth_identity")
    with op.batch_alter_table("app_user") as batch:
        batch.drop_column("password_auth_enabled")
