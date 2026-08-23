"""add authentication sessions

Revision ID: 2d45f5ae288f
Revises: 6f0aad6e5b27
Create Date: 2026-08-19 22:25:38.980001

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "2d45f5ae288f"
down_revision: Union[str, Sequence[str], None] = "6f0aad6e5b27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column("username_normalized", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "app_user",
        sa.Column("role", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "app_user",
        sa.Column("totp_enabled", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "app_user",
        sa.Column("banned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "app_user",
        sa.Column("ban_reason", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "app_user",
        sa.Column("banned_by_user_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "app_user",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(
        sa.text(
            "UPDATE app_user SET "
            "username_normalized = lower(username), "
            "role = 'user', "
            "totp_enabled = false, "
            "password_changed_at = created_at"
        )
    )

    connection = op.get_bind()
    duplicates = connection.execute(
        sa.text(
            "SELECT lower(username) FROM app_user "
            "GROUP BY lower(username) HAVING count(*) > 1"
        )
    ).first()
    if duplicates is not None:
        raise RuntimeError(
            "cannot migrate users with case-insensitive username conflicts"
        )

    with op.batch_alter_table("app_user") as batch_op:
        batch_op.alter_column("username_normalized", nullable=False)
        batch_op.alter_column("role", nullable=False)
        batch_op.alter_column("totp_enabled", nullable=False)
        batch_op.alter_column("password_changed_at", nullable=False)
        batch_op.create_unique_constraint(
            op.f("uq_app_user_username_normalized"),
            ["username_normalized"],
        )
        batch_op.create_foreign_key(
            op.f("fk_app_user_banned_by_user_id_app_user"),
            "app_user",
            ["banned_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            op.f("ck_app_user_ban_reason_when_banned"),
            "banned_at IS NULL OR ban_reason IS NOT NULL",
        )
        batch_op.create_check_constraint(
            op.f("ck_app_user_totp_secret_when_enabled"),
            "NOT totp_enabled OR totp_secret_ciphertext IS NOT NULL",
        )
        batch_op.create_check_constraint(
            op.f("ck_app_user_valid_role"),
            "role IN ('user', 'admin')",
        )

    op.create_table(
        "auth_session",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=64), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_auth_session_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_session")),
    )
    op.create_index(
        "ix_auth_session_expires_at",
        "auth_session",
        ["absolute_expires_at"],
    )
    op.create_index(
        "ix_auth_session_user_revoked",
        "auth_session",
        ["user_id", "revoked_at"],
    )

    op.create_table(
        "auth_refresh_token",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("parent_token_id", sa.Uuid(), nullable=True),
        sa.Column("replaced_by_token_id", sa.Uuid(), nullable=True),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_token_id"],
            ["auth_refresh_token.id"],
            name=op.f(
                "fk_auth_refresh_token_parent_token_id_auth_refresh_token"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_token_id"],
            ["auth_refresh_token.id"],
            name=op.f(
                "fk_auth_refresh_token_replaced_by_token_id_auth_refresh_token"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["auth_session.id"],
            name=op.f("fk_auth_refresh_token_session_id_auth_session"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_refresh_token")),
        sa.UniqueConstraint(
            "parent_token_id",
            name=op.f("uq_auth_refresh_token_parent_token_id"),
        ),
        sa.UniqueConstraint(
            "token_hash",
            name=op.f("uq_auth_refresh_token_token_hash"),
        ),
    )
    op.create_index(
        "ix_auth_refresh_token_expires_at",
        "auth_refresh_token",
        ["expires_at"],
    )
    op.create_index(
        "ix_auth_refresh_token_session_issued",
        "auth_refresh_token",
        ["session_id", "issued_at"],
    )

    op.create_index(
        "ix_security_audit_actor_occurred",
        "security_audit_event",
        ["actor_user_id", "occurred_at"],
    )
    op.create_index(
        "ix_security_audit_target_occurred",
        "security_audit_event",
        ["target_type", "target_id", "occurred_at"],
    )
    op.create_index(
        "ix_security_audit_event_occurred",
        "security_audit_event",
        ["event_type", "occurred_at"],
    )
    op.create_index(
        "ix_security_audit_request_id",
        "security_audit_event",
        ["request_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_security_audit_request_id",
        table_name="security_audit_event",
    )
    op.drop_index(
        "ix_security_audit_event_occurred",
        table_name="security_audit_event",
    )
    op.drop_index(
        "ix_security_audit_target_occurred",
        table_name="security_audit_event",
    )
    op.drop_index(
        "ix_security_audit_actor_occurred",
        table_name="security_audit_event",
    )

    op.drop_index(
        "ix_auth_refresh_token_session_issued",
        table_name="auth_refresh_token",
    )
    op.drop_index(
        "ix_auth_refresh_token_expires_at",
        table_name="auth_refresh_token",
    )
    op.drop_table("auth_refresh_token")
    op.drop_index("ix_auth_session_user_revoked", table_name="auth_session")
    op.drop_index("ix_auth_session_expires_at", table_name="auth_session")
    op.drop_table("auth_session")

    with op.batch_alter_table("app_user") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_app_user_valid_role"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_app_user_totp_secret_when_enabled"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_app_user_ban_reason_when_banned"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("fk_app_user_banned_by_user_id_app_user"),
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            op.f("uq_app_user_username_normalized"),
            type_="unique",
        )
        batch_op.drop_column("password_changed_at")
        batch_op.drop_column("banned_by_user_id")
        batch_op.drop_column("ban_reason")
        batch_op.drop_column("banned_at")
        batch_op.drop_column("totp_enabled")
        batch_op.drop_column("role")
        batch_op.drop_column("username_normalized")
