"""award policy version manual_only

Revision ID: a3c7e29f6b41
Revises: e51f3a8d92b6
Create Date: 2026-09-09 00:00:00.000000

Additive: a new column with a server_default, so it deploys before the
code that reads it without changing anything for existing rows (all
default to manual_only=False, i.e. today's behavior).

For an award with no computable rule at all — Leadership, Best in
Subject — where eligibility is a nomination, not a threshold. Without
this, a version with no thresholds set reads as "nothing to fail" and
award_service._evaluate awards it to everyone.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a3c7e29f6b41"
down_revision: Union[str, Sequence[str], None] = "e51f3a8d92b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "award_policy_versions",
        sa.Column("manual_only", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("award_policy_versions", "manual_only")
