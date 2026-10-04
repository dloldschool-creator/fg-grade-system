"""Add LEARNER_CONTACTS to the import job type enum (spec §78.1)

The parent/guardian contact update import records its runs in
``import_jobs`` like every other import, so its job type has to exist in
the ``importjobtype`` enum.

Additive: ``ADD VALUE`` changes no row, and the running app never writes
the new value until the code that knows about it is deployed. Run in an
autocommit block — Postgres refuses to *use* an enum value added in the
same transaction, and older versions refuse the ADD itself inside one.

Downgrade is a no-op: Postgres cannot drop a value from an enum without
rebuilding the type, and an unused value does no harm.

Revision ID: e9c4a2b81d35
Revises: d8b3f5a17c24
Create Date: 2026-10-04
"""

from alembic import op

revision = "e9c4a2b81d35"
down_revision = "d8b3f5a17c24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE importjobtype ADD VALUE IF NOT EXISTS 'LEARNER_CONTACTS'")


def downgrade() -> None:
    pass
