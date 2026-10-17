"""Attendance months: a change made after finalizing, from outside the grid

Three nullable columns on ``attendance_month_status``:
``changed_after_finalize_at``, ``changed_after_finalize_by_user_id`` and
``change_after_finalize_note``.

A finalized month's printed SF2/SF4 can go out of date without the month
being reopened: an Academic Calendar day flipped, or a learner movement
logged or removed with a date that reaches back into it. Only a reopen
raised the "reprint the SF2/SF4" flag (spec §33), so those changes went
unannounced. The flag is now pending while the later of ``reopened_at``
and ``changed_after_finalize_at`` is later than ``reprint_acknowledged_at``.

Kept apart from the reopen columns on purpose: writing a calendar change
into ``reopened_at`` would leave ``reopen_reason`` describing an event that
did not happen.

Every existing row is NULL, which is correct: nothing before this
migration recorded such a change, and no data is rewritten.

Additive: three nullable columns. Safe to apply before the code that uses
them, and the running code ignores them.

Revision ID: c5d8e1f2a947
Revises: b4e2c7a91d36
Create Date: 2026-10-17
"""

import sqlalchemy as sa
from alembic import op

revision = "c5d8e1f2a947"
down_revision = "b4e2c7a91d36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attendance_month_status",
        sa.Column("changed_after_finalize_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "attendance_month_status",
        sa.Column("changed_after_finalize_by_user_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "attendance_month_status",
        sa.Column("change_after_finalize_note", sa.String(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_attendance_month_status_changed_after_finalize_by_user_id_users"),
        "attendance_month_status",
        "users",
        ["changed_after_finalize_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_attendance_month_status_changed_after_finalize_by_user_id_users"),
        "attendance_month_status",
        type_="foreignkey",
    )
    op.drop_column("attendance_month_status", "change_after_finalize_note")
    op.drop_column("attendance_month_status", "changed_after_finalize_by_user_id")
    op.drop_column("attendance_month_status", "changed_after_finalize_at")
