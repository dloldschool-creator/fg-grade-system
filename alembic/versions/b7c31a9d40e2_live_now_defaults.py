"""Give fourteen timestamp columns a live now() instead of a frozen constant

Fourteen columns were declared in the models as
``mapped_column(server_default="now()")`` — a plain Python string. SQLAlchemy
treats a string ``server_default`` as a literal SQL value, so the DDL went out
as ``DEFAULT 'now()'`` (quoted) rather than ``DEFAULT now()`` (a call).
Postgres resolved that quoted value **once, when the migration ran**, and
stored the answer:

    audit_logs.created_at DEFAULT '2026-08-10 04:31:28.755305'::timestamp

Every row inserted since has therefore claimed to have been created at that
same instant. It was found on the Audit Log page, where all seven entries —
days apart in reality — showed one identical timestamp.

That makes it a correctness bug rather than a cosmetic one: CLAUDE.md rule 8
requires every sensitive change to record *when*, and §50 requires the history
to be answerable after the fact. A log that times everything identically
cannot order two edits to the same grade.

``TimestampMixin`` in ``app/models/base.py`` was always correct
(``server_default=func.now()``), which is why the tables that inherit it —
enrollments, attendance_records, term_grades — were never affected. Only these
fourteen standalone declarations were wrong.

The existing rows keep their false timestamps. The true times were never
recorded anywhere, so there is nothing to restore them from, and inventing
plausible ones would be worse than leaving them visibly identical.

Revision ID: b7c31a9d40e2
Revises: f00c90460cb9
Create Date: 2026-08-13
"""

from alembic import op

revision = "b7c31a9d40e2"
down_revision = "f00c90460cb9"
branch_labels = None
depends_on = None


# (table, column) — every column whose default was frozen. Verified against
# information_schema before writing this: exactly these fourteen had a
# '…'::timestamp default, and no other column in the database did.
COLUMNS = [
    ("audit_logs", "created_at"),
    ("award_policy_versions", "created_at"),
    ("export_jobs", "created_at"),
    ("grade_finalization_records", "created_at"),
    ("grading_policy_versions", "created_at"),
    ("import_jobs", "created_at"),
    ("learner_academic_records", "snapshot_at"),
    ("learner_awards", "computed_at"),
    ("learner_awards", "created_at"),
    ("learner_movements", "created_at"),
    ("report_generation_logs", "generated_at"),
    ("report_templates", "created_at"),
    ("teacher_assignments", "assigned_at"),
    ("user_roles", "created_at"),
]


def upgrade() -> None:
    # ALTER COLUMN … SET DEFAULT rewrites catalogue metadata only: no table
    # scan, no rewrite, no lock held beyond the statement. Safe to run while
    # the app is serving, which matters — it is live.
    for table, column in COLUMNS:
        op.execute(f'ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT now()')


def downgrade() -> None:
    # Deliberately restores a live now() rather than the frozen constant.
    # Reverting to a broken default to be faithful to history would put the
    # bug back; there is no version of this worth returning to.
    for table, column in COLUMNS:
        op.execute(f'ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT now()')
