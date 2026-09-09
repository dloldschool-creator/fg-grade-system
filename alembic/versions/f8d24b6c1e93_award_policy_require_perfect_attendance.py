"""award policy require_perfect_attendance

Revision ID: f8d24b6c1e93
Revises: a3c7e29f6b41
Create Date: 2026-09-09 00:00:00.000000

Additive: a new column with a server_default, so it deploys before the
code that reads it without changing anything for existing rows (all
default to require_perfect_attendance=False, i.e. today's behavior).

Lets an award policy version require zero absences and zero
tardies/cutting over its scope's period (a term, or the whole year for
ANNUAL), with attendance fully encoded first.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f8d24b6c1e93"
down_revision: Union[str, Sequence[str], None] = "a3c7e29f6b41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "award_policy_versions",
        sa.Column("require_perfect_attendance", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("award_policy_versions", "require_perfect_attendance")
