"""Parent/guardian contact on learners (spec §78.1)

Five columns on ``learners``: guardian name, email and mobile, whether the
parent has consented to school notices, and the date that was recorded.

Additive — four nullable columns and one NOT NULL boolean with a constant
server default (``false``), which Postgres applies without rewriting the
table. The running app ignores columns it does not know about, so this is
safe to apply before the code that reads it. Nothing is backfilled: every
existing learner reads as "no contact, no consent", which is the fail-safe
direction — nothing can be sent to anyone until someone records both.

Revision ID: d8b3f5a17c24
Revises: 61679070270f
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "d8b3f5a17c24"
down_revision = "61679070270f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("learners", sa.Column("guardian_name", sa.String(), nullable=True))
    op.add_column("learners", sa.Column("guardian_email", sa.String(), nullable=True))
    op.add_column("learners", sa.Column("guardian_mobile", sa.String(13), nullable=True))
    op.add_column(
        "learners",
        sa.Column("notices_consent", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("learners", sa.Column("notices_consent_date", sa.Date(), nullable=True))
    op.create_check_constraint(
        op.f("ck_learners_guardian_mobile_format"),
        "learners",
        "guardian_mobile IS NULL OR guardian_mobile ~ '^\\+639[0-9]{9}$'",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_learners_guardian_mobile_format"), "learners", type_="check")
    op.drop_column("learners", "notices_consent_date")
    op.drop_column("learners", "notices_consent")
    op.drop_column("learners", "guardian_mobile")
    op.drop_column("learners", "guardian_email")
    op.drop_column("learners", "guardian_name")
