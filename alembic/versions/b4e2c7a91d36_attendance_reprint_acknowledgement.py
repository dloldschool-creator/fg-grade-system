"""Attendance months: who confirmed the SF2/SF4 was reprinted after a reopen

Two nullable columns on ``attendance_month_status``:
``reprint_acknowledged_at`` and ``reprint_acknowledged_by_user_id``.

A reopened month carries a "reprint the SF2/SF4" flag (spec §33). Nothing
recorded whether anyone did, so the flag could never clear and the
Dashboard count only grew. The flag is now pending while ``reopened_at`` is
later than ``reprint_acknowledged_at`` (or the latter is NULL), so a second
reopen brings it back without anything having to reset these columns.

Every existing row is NULL/NULL, which is correct: a month reopened before
this migration has no acknowledgement and stays flagged until someone gives
one — no data is rewritten.

Additive: two nullable columns. Safe to apply before the code that uses
them, and the running code ignores them.

Revision ID: b4e2c7a91d36
Revises: a1d7e3c95f28
Create Date: 2026-10-15
"""

import sqlalchemy as sa
from alembic import op

revision = "b4e2c7a91d36"
down_revision = "a1d7e3c95f28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attendance_month_status",
        sa.Column("reprint_acknowledged_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "attendance_month_status",
        sa.Column("reprint_acknowledged_by_user_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_attendance_month_status_reprint_acknowledged_by_user_id_users"),
        "attendance_month_status",
        "users",
        ["reprint_acknowledged_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_attendance_month_status_reprint_acknowledged_by_user_id_users"),
        "attendance_month_status",
        type_="foreignkey",
    )
    op.drop_column("attendance_month_status", "reprint_acknowledged_by_user_id")
    op.drop_column("attendance_month_status", "reprint_acknowledged_at")
