"""Preserve requests mapped to active tasks merged by the coordination migration.

Revision ID: 21c5b0e8d9a4
Revises: 0f4c2a9d8b71
"""

from alembic import op

revision = "21c5b0e8d9a4"
down_revision = "0f4c2a9d8b71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the archived duplicate rows for history, but replay requests against
    # the surviving active task. Completed work must not be queued again.
    op.execute("""
        UPDATE idempotency_record
        SET task_id = (
            SELECT survivor.id FROM domain_refresh_task duplicate
            JOIN domain_refresh_task survivor
              ON survivor.managed_domain_id = duplicate.managed_domain_id
             AND survivor.status IN ('queued', 'running')
             AND survivor.created_at <= duplicate.completed_at
            WHERE duplicate.id = idempotency_record.task_id
              AND duplicate.error_code = 'duplicate_merged'
        )
        WHERE EXISTS (
            SELECT 1 FROM domain_refresh_task duplicate
            JOIN domain_refresh_task survivor
              ON survivor.managed_domain_id = duplicate.managed_domain_id
             AND survivor.status IN ('queued', 'running')
             AND survivor.created_at <= duplicate.completed_at
            WHERE duplicate.id = idempotency_record.task_id
              AND duplicate.error_code = 'duplicate_merged'
        )
    """)
    op.execute("""
        UPDATE domain_refresh_task
        SET force_refresh = TRUE
        WHERE status = 'queued' AND EXISTS (
            SELECT 1 FROM domain_refresh_task duplicate
            WHERE duplicate.managed_domain_id = domain_refresh_task.managed_domain_id
              AND duplicate.error_code = 'duplicate_merged'
              AND duplicate.force_refresh = TRUE
              AND domain_refresh_task.created_at <= duplicate.completed_at
        )
    """)


def downgrade() -> None:
    # Merged idempotency associations cannot be split unambiguously. No schema
    # changes were made, and preserving the surviving association is safe.
    pass
