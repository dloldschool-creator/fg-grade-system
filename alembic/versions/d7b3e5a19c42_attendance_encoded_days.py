"""Attendance: store a saved P as a bit, not a row

New table ``attendance_encoded_days``: one row per enrollment per month,
with ``days`` a bitmask of the days of that month that have been encoded.
A set bit with no ``attendance_records`` row is a P; a clear bit with no
row is "not yet encoded", exactly as a missing row was before (§33).

Why: checked 2026-10-29, ``attendance_records`` was 13 MB for 51,159
rows, 96% of them plain PRESENT — a row per learner per class day, about
63 MB a year at ~1,200 learners against a 500 MB database. One row here
stands for a month.

Additive: a new table, nothing existing is altered and no data is
rewritten. The running code ignores it. The new code reads a day as
encoded when its bit is set *or* it has a row, so existing PRESENT rows
keep reading correctly before and after this migration. Moving them into
bits and deleting them is ``scripts/compact_attendance.py``, run once
after the new code is live — not this migration.

Revision ID: d7b3e5a19c42
Revises: f1a8c3d92e57
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "d7b3e5a19c42"
down_revision = "f1a8c3d92e57"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attendance_encoded_days",
        sa.Column("enrollment_id", sa.UUID(), nullable=False),
        sa.Column("year_month", sa.Date(), nullable=False),
        sa.Column("days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_saved_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["enrollment_id"],
            ["enrollments.id"],
            name=op.f("fk_attendance_encoded_days_enrollment_id_enrollments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_saved_by_user_id"],
            ["users.id"],
            name=op.f("fk_attendance_encoded_days_last_saved_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attendance_encoded_days")),
        sa.UniqueConstraint(
            "enrollment_id", "year_month", name=op.f("uq_attendance_encoded_days_enrollment_id")
        ),
    )


def downgrade() -> None:
    # Only safe before scripts/compact_attendance.py has deleted any
    # PRESENT rows: after that, these bits are the only record of them.
    op.drop_table("attendance_encoded_days")
