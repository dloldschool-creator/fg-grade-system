"""award certificate customisation

Revision ID: b4e6d1f92a58
Revises: a7d2e91c4b60
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b4e6d1f92a58"
down_revision: Union[str, Sequence[str], None] = "a7d2e91c4b60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # op.add_column does NOT auto-create a new enum type the way
    # create_table does, so certificatelayout has to be created explicitly.
    certificate_layout = postgresql.ENUM(
        "ONE_PER_PAGE", "TWO_PER_PAGE", name="certificatelayout", create_type=False
    )
    certificate_layout.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "award_policy_versions",
        sa.Column(
            "certificate_layout", certificate_layout, server_default="ONE_PER_PAGE", nullable=False
        ),
    )
    op.add_column(
        "award_policy_versions", sa.Column("certificate_body_template", sa.Text(), nullable=True)
    )
    op.add_column(
        "award_policy_versions",
        sa.Column(
            "signatory_overrides", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )

    # Data fix, not schema: the new column defaults every existing row to
    # ONE_PER_PAGE, but the Awards page's batch button has always rendered
    # a TERM-scoped policy (the tiered Honors — classroom-level recognition)
    # two certificates to a page, matching what
    # app/certificate_generator.py's own module docstring documents as the
    # intended split (one-per-page for the official Academic Excellence
    # issuance, two-per-page for classroom recognition). Matched by scope
    # rather than by name since that's the durable distinction the code
    # already draws.
    op.execute(
        "UPDATE award_policy_versions SET certificate_layout = 'TWO_PER_PAGE' WHERE scope = 'TERM'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("award_policy_versions", "signatory_overrides")
    op.drop_column("award_policy_versions", "certificate_body_template")
    op.drop_column("award_policy_versions", "certificate_layout")
    # Drop the type this migration created.
    postgresql.ENUM(name="certificatelayout").drop(op.get_bind(), checkfirst=True)
