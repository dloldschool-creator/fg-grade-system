"""Track when a month's attendance grid was last saved, and by whom

Two nullable columns on ``attendance_month_status``, matching the
``finalized_at``/``finalized_by_user_id`` and
``reopened_at``/``reopened_by_user_id`` pairs already on this table.

**Why this and not session state.** The Attendance page's finalization
panel needs to warn when a day was overridden after the last grid save —
otherwise an adviser can finalize a month that quietly changed underneath
them. A first version tracked "last saved" in Streamlit's
``st.session_state``, which only ever knows about saves made in the same
browser tab: it forgets the moment the tab closes or a different adviser
(or a different device) opens the page. Two advisers can hold the same
section, and a Registrar or Super Admin may finalize a month neither of
them prepared. The comparison only means anything if "last saved" is a
fact about the month, not about one browser's memory of it — so it moves
onto the row the rest of this table's own lifecycle timestamps already
live on.

Stamped inside ``_save_grid``'s existing commit whenever it actually
writes a change (not on a no-op save), so the two facts — "attendance
changed" and "this is when we last knew about it" — can never drift apart
by landing in separate transactions.

``ON DELETE SET NULL``, matching every other ``*_user_id`` column on this
table: retiring a teacher's account must not be blocked by, or erase the
history of, a month they once saved.

Additive — two nullable columns, no backfill, no rewrite, and the running
app ignores columns it does not know about. Safe to apply before the code
that reads it.

Revision ID: 61679070270f
Revises: f8d24b6c1e93
Create Date: 2026-09-15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "61679070270f"
down_revision = "f8d24b6c1e93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attendance_month_status",
        sa.Column("last_saved_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "attendance_month_status",
        sa.Column("last_saved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_attendance_month_status_last_saved_by_user_id_users",
        "attendance_month_status",
        "users",
        ["last_saved_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_attendance_month_status_last_saved_by_user_id_users",
        "attendance_month_status",
        type_="foreignkey",
    )
    op.drop_column("attendance_month_status", "last_saved_by_user_id")
    op.drop_column("attendance_month_status", "last_saved_at")
