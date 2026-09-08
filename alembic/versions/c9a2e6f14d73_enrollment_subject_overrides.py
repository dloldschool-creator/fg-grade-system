"""enrollment subject overrides

Revision ID: c9a2e6f14d73
Revises: b4e6d1f92a58
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9a2e6f14d73"
down_revision: Union[str, Sequence[str], None] = "b4e6d1f92a58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "enrollment_subject_overrides",
        sa.Column("enrollment_id", sa.UUID(), nullable=False),
        sa.Column("original_section_subject_offering_id", sa.UUID(), nullable=False),
        sa.Column("substitute_section_subject_offering_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["enrollment_id"], ["enrollments.id"],
            name=op.f("fk_enrollment_subject_overrides_enrollment_id_enrollments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["original_section_subject_offering_id"], ["section_subject_offerings.id"],
            name=op.f(
                "fk_enrollment_subject_overrides_original_section_subject_offering_id_section_subject_offerings"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["substitute_section_subject_offering_id"], ["section_subject_offerings.id"],
            name=op.f(
                "fk_enrollment_subject_overrides_substitute_section_subject_offering_id_section_subject_offerings"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name=op.f("fk_enrollment_subject_overrides_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_enrollment_subject_overrides")),
        sa.UniqueConstraint(
            "enrollment_id", "original_section_subject_offering_id",
            name=op.f("uq_enrollment_subject_overrides_enrollment_id"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("enrollment_subject_overrides")
