"""merge refresh task and notification heads

Revision ID: b5c9d1e3f702
Revises: a2b7d4e8f901, e1a2b3c4d5e6
Create Date: 2026-08-25 00:00:00.000000
"""

from collections.abc import Sequence

revision: str = "b5c9d1e3f702"
down_revision: tuple[str, str] | Sequence[str] | None = (
    "a2b7d4e8f901",
    "e1a2b3c4d5e6",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
