"""Record who created each learner

One nullable column on ``learners``.

**Why a column and not the audit log.** An adviser's right to edit a
learner comes from the section they advise (§3C, §54). A learner with no
enrollment is in nobody's section — and the bulk-add panel on the Learner
Masterlist deliberately creates learners even when it refuses the Section
named in the file, so an adviser can produce exactly that. Without a
record of who typed the row in, the adviser who has just added forty
learners loses the ability to correct a misspelt name the moment the page
reloads. ``audit_logs`` cannot answer it either: nothing has ever written
a learner-creation entry.

**NULL is not "everyone".** Every one of the ~1,200 learners already in
the table predates this column and stays NULL, and the access rule reads
NULL as unowned — editable by a Registrar or Super Admin only. There is
nothing to backfill from and guessing an owner would hand out edit rights
that nobody granted, so the fail-safe direction is to hand out none.

``ON DELETE SET NULL``, matching ``import_jobs.uploaded_by_user_id`` and
``audit_logs.user_id``: retiring a teacher's account must neither be
blocked by the learners they enrolled nor delete them.

Additive — one nullable column, no rewrite, no backfill, and the running
app ignores columns it does not know about. Safe to apply before the code
that reads it.

Revision ID: a7d2e91c4b60
Revises: c3f1a7d90b42
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7d2e91c4b60"
down_revision = "c3f1a7d90b42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "learners",
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_learners_created_by_user_id_users",
        "learners",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # The adviser scope query filters on this column and on nothing else
    # for its second half ("learners I created that are not enrolled"), so
    # it runs on every render of the Learner Masterlist.
    op.create_index(
        "ix_learners_created_by_user_id",
        "learners",
        ["created_by_user_id"],
        postgresql_where=sa.text("created_by_user_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_learners_created_by_user_id", table_name="learners")
    op.drop_constraint("fk_learners_created_by_user_id_users", "learners", type_="foreignkey")
    op.drop_column("learners", "created_by_user_id")
