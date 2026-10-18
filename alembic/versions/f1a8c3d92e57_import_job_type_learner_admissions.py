"""Add LEARNER_ADMISSIONS to the import job type enum

The SHS admission record upload (Learner Masterlist) records its runs in
``import_jobs`` like every other import, so its job type has to exist in
the ``importjobtype`` enum.

Additive: ``ADD VALUE`` changes no row, and the running app never writes
the new value until the code that knows about it is deployed -- so apply
this BEFORE rebooting the new code. Run in an autocommit block: Postgres
refuses to *use* an enum value added in the same transaction, and older
versions refuse the ADD itself inside one.

Downgrade is a no-op: Postgres cannot drop a value from an enum without
rebuilding the type, and an unused value does no harm.

Revision ID: f1a8c3d92e57
Revises: c5d8e1f2a947
Create Date: 2026-10-18
"""

from alembic import op

revision = "f1a8c3d92e57"
down_revision = "c5d8e1f2a947"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE importjobtype ADD VALUE IF NOT EXISTS 'LEARNER_ADMISSIONS'")


def downgrade() -> None:
    pass
