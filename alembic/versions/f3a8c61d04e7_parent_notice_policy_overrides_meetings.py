"""Parent notices: release policy, adviser overrides, meeting schedule (spec §78)

Three new tables, nothing existing touched:

- ``parent_notice_policies`` — the versioned Release thresholds per school
  year (§78.2). Seeded with version 1 for every existing school year at
  the values the school approved on 2026-10-04: at most 2 absences, at
  most 2 lates, no cutting.
- ``parent_notice_overrides`` — an adviser moving a learner between
  Release and Concern for a term, with a required reason (§78.3).
- ``parent_meeting_schedules`` — the meeting date and time printed on the
  concern letter: one per section and term, plus per-learner changes
  (§78.5). Two partial unique indexes keep one of each.

Additive: new tables only, so the running app is unaffected and this is
safe to apply before the code that reads it.

Revision ID: f3a8c61d04e7
Revises: e9c4a2b81d35
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "f3a8c61d04e7"
down_revision = "e9c4a2b81d35"
branch_labels = None
depends_on = None


def _timestamps_and_version():
    return [
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "parent_notice_policies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("school_year_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False),
        sa.Column("max_absences", sa.SmallInteger(), nullable=False),
        sa.Column("max_lates", sa.SmallInteger(), nullable=False),
        sa.Column("max_cuttings", sa.SmallInteger(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["school_year_id"], ["school_years.id"],
            name=op.f("fk_parent_notice_policies_school_year_id_school_years"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name=op.f("fk_parent_notice_policies_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_parent_notice_policies")),
        sa.UniqueConstraint(
            "school_year_id", "version", name=op.f("uq_parent_notice_policies_school_year_id")
        ),
        sa.CheckConstraint(
            "max_absences >= 0 AND max_lates >= 0 AND max_cuttings >= 0",
            name=op.f("ck_parent_notice_policies_thresholds_not_negative"),
        ),
    )

    op.create_table(
        "parent_notice_overrides",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("enrollment_id", sa.UUID(), nullable=False),
        sa.Column("term_id", sa.UUID(), nullable=False),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("set_by_user_id", sa.UUID(), nullable=True),
        *_timestamps_and_version(),
        sa.ForeignKeyConstraint(
            ["enrollment_id"], ["enrollments.id"],
            name=op.f("fk_parent_notice_overrides_enrollment_id_enrollments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["term_id"], ["terms.id"],
            name=op.f("fk_parent_notice_overrides_term_id_terms"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["set_by_user_id"], ["users.id"],
            name=op.f("fk_parent_notice_overrides_set_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_parent_notice_overrides")),
        sa.UniqueConstraint(
            "enrollment_id", "term_id", name=op.f("uq_parent_notice_overrides_enrollment_id")
        ),
        sa.CheckConstraint(
            "decision IN ('RELEASE', 'CONCERN')",
            name=op.f("ck_parent_notice_overrides_decision_valid"),
        ),
        sa.CheckConstraint(
            "length(btrim(reason)) > 0",
            name=op.f("ck_parent_notice_overrides_reason_required"),
        ),
    )

    op.create_table(
        "parent_meeting_schedules",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("section_id", sa.UUID(), nullable=False),
        sa.Column("term_id", sa.UUID(), nullable=False),
        sa.Column("enrollment_id", sa.UUID(), nullable=True),
        sa.Column("meeting_date", sa.Date(), nullable=False),
        sa.Column("meeting_time", sa.Time(), nullable=False),
        sa.Column("set_by_user_id", sa.UUID(), nullable=True),
        *_timestamps_and_version(),
        sa.ForeignKeyConstraint(
            ["section_id"], ["sections.id"],
            name=op.f("fk_parent_meeting_schedules_section_id_sections"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["term_id"], ["terms.id"],
            name=op.f("fk_parent_meeting_schedules_term_id_terms"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["enrollment_id"], ["enrollments.id"],
            name=op.f("fk_parent_meeting_schedules_enrollment_id_enrollments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["set_by_user_id"], ["users.id"],
            name=op.f("fk_parent_meeting_schedules_set_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_parent_meeting_schedules")),
    )
    op.create_index(
        "uq_parent_meeting_section_term",
        "parent_meeting_schedules",
        ["section_id", "term_id"],
        unique=True,
        postgresql_where=sa.text("enrollment_id IS NULL"),
    )
    op.create_index(
        "uq_parent_meeting_enrollment_term",
        "parent_meeting_schedules",
        ["enrollment_id", "term_id"],
        unique=True,
        postgresql_where=sa.text("enrollment_id IS NOT NULL"),
    )

    # Version 1 for every school year that already exists, at the values
    # the school approved. A school year created later needs its own row.
    op.execute(
        "INSERT INTO parent_notice_policies "
        "(id, school_year_id, version, max_absences, max_lates, max_cuttings) "
        "SELECT gen_random_uuid(), id, 1, 2, 2, 0 FROM school_years"
    )


def downgrade() -> None:
    op.drop_index("uq_parent_meeting_enrollment_term", table_name="parent_meeting_schedules")
    op.drop_index("uq_parent_meeting_section_term", table_name="parent_meeting_schedules")
    op.drop_table("parent_meeting_schedules")
    op.drop_table("parent_notice_overrides")
    op.drop_table("parent_notice_policies")
