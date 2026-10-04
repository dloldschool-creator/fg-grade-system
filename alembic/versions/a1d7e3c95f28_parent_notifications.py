"""Parent notices: the sent record (spec §78.4, §78.6)

One new table, ``parent_notifications``: every term-card or concern
notice sent to a parent, or attempted, by email, SMS link or printed
letter.

The partial unique index ``uq_parent_notifications_one_live_email`` is the
double-send guard: at most one PENDING-or-SENT email of each kind per
learner and term. The sender commits a PENDING row before contacting the
mail server, so concurrent or repeated sends lose at the database.

Additive: a new table only. Safe to apply before the code that uses it.

Revision ID: a1d7e3c95f28
Revises: f3a8c61d04e7
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "a1d7e3c95f28"
down_revision = "f3a8c61d04e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parent_notifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("enrollment_id", sa.UUID(), nullable=False),
        sa.Column("term_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("recipient", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("basis_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("sent_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["enrollment_id"], ["enrollments.id"],
            name=op.f("fk_parent_notifications_enrollment_id_enrollments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["term_id"], ["terms.id"],
            name=op.f("fk_parent_notifications_term_id_terms"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sent_by_user_id"], ["users.id"],
            name=op.f("fk_parent_notifications_sent_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_parent_notifications")),
        sa.CheckConstraint(
            "kind IN ('TERM_CARD', 'CONCERN')", name=op.f("ck_parent_notifications_kind_valid")
        ),
        sa.CheckConstraint(
            "channel IN ('EMAIL', 'SMS', 'LETTER')",
            name=op.f("ck_parent_notifications_channel_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SENT', 'FAILED', 'SUPERSEDED')",
            name=op.f("ck_parent_notifications_status_valid"),
        ),
    )
    op.create_index(
        "uq_parent_notifications_one_live_email",
        "parent_notifications",
        ["enrollment_id", "term_id", "kind"],
        unique=True,
        postgresql_where=sa.text("channel = 'EMAIL' AND status IN ('PENDING', 'SENT')"),
    )
    op.create_index(
        "ix_parent_notifications_enrollment_term",
        "parent_notifications",
        ["enrollment_id", "term_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_parent_notifications_enrollment_term", table_name="parent_notifications")
    op.drop_index("uq_parent_notifications_one_live_email", table_name="parent_notifications")
    op.drop_table("parent_notifications")
