"""DO 017 s. 2026 unit system: units, averaging rules, and their audit trail

Revision ID: c3f1a7d90b42
Revises: f8a3d05c1b27
Create Date: 2026-08-20

DepEd Order 017 s. 2026 (Strengthened SHS Curriculum), Annex E, makes the
Term Average and the General Average **unit-weighted**. This migration adds
where units come from, which averaging rules are in force, and enough of an
audit trail to explain any average after the fact.

**Entirely additive, so it deploys before the code** (docs/operations.md):
every new column is nullable or carries a server default, and every default
reproduces the pre-DO-017 behaviour. Running this migration on its own
changes no grade, no average and no report — the arithmetic only moves when
someone activates a policy version that opts in, which is
`scripts/apply_do17_units.py`, not this file.

Deliberately no data backfill here. Units are school data, not schema, and
writing them from a migration would put a curriculum decision somewhere that
can only be undone by another migration.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c3f1a7d90b42"
down_revision: Union[str, Sequence[str], None] = "f8a3d05c1b27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AVERAGING_METHOD = postgresql.ENUM(
    "UNWEIGHTED", "UNIT_WEIGHTED", name="averagingmethod", create_type=False
)


def _units(name: str) -> sa.Column:
    return sa.Column(name, sa.Numeric(precision=5, scale=2), nullable=True)


def _total_units(name: str = "total_units") -> sa.Column:
    return sa.Column(name, sa.Numeric(precision=7, scale=2), nullable=True)


def _unrounded() -> sa.Column:
    return sa.Column("unrounded_final_grade", sa.Numeric(precision=9, scale=4), nullable=True)


def _method_column() -> sa.Column:
    return sa.Column(
        "averaging_method", AVERAGING_METHOD, server_default="UNWEIGHTED", nullable=False
    )


def upgrade() -> None:
    """Upgrade schema."""
    # `op.add_column` does NOT create a new enum type on the way past — that
    # has to happen first, or every add_column below fails on an undefined
    # type (CLAUDE.md, "Alembic enums, two gotchas").
    postgresql.ENUM("UNWEIGHTED", "UNIT_WEIGHTED", name="averagingmethod").create(
        op.get_bind(), checkfirst=True
    )

    # --- Where units come from (DO 017 Table 19) -------------------------
    # Broadest to narrowest; app/curriculum_policy.py:load_offering_units
    # resolves them in the opposite order.
    op.add_column("subject_categories", _units("units_per_term"))
    op.add_column("subjects", _units("units_per_term"))
    op.add_column(
        "subjects", sa.Column("instructional_hours_per_year", sa.SmallInteger(), nullable=True)
    )
    op.add_column("section_subject_offerings", _units("units_per_term"))
    # The combined language pair's weight as ONE learning area.
    op.add_column("combined_learning_areas", _units("units_per_term"))

    # --- Which averaging rules are in force ------------------------------
    op.add_column(
        "grading_policy_versions",
        sa.Column("effective_grade_level_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        # Short explicit name: the project's naming convention would produce
        # 64 characters here and Postgres silently truncates at 63.
        "fk_gpv_effective_grade_level_grade_levels",
        "grading_policy_versions",
        "grade_levels",
        ["effective_grade_level_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column("grading_policy_versions", _method_column())
    op.add_column(
        "grading_policy_versions",
        sa.Column(
            "combine_language_pair_in_term_average",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.add_column(
        "grading_policy_versions",
        sa.Column(
            "average_from_unrounded_finals",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )

    # --- What each average was actually built from -----------------------
    for table in ("subject_final_grades", "combined_learning_area_results"):
        op.add_column(table, _units("units_per_term"))
        op.add_column(table, _total_units("units"))
        op.add_column(table, _unrounded())

    for table in ("term_grade_summaries", "annual_grade_summaries"):
        op.add_column(table, _method_column())
        op.add_column(table, _total_units())

    # --- Frozen into the permanent record (§38) --------------------------
    # Text and numbers, never references: a later revision of Table 19 must
    # not re-explain a General Average that has already been issued.
    op.add_column(
        "learner_academic_records", sa.Column("averaging_method", sa.String(), nullable=True)
    )
    op.add_column("learner_academic_records", _total_units())
    op.add_column("learner_academic_record_subjects", _units("units_per_term"))
    op.add_column("learner_academic_record_subjects", _total_units("units"))
    op.add_column("learner_academic_record_subjects", _unrounded())
    op.add_column(
        "learner_academic_record_terms", sa.Column("averaging_method", sa.String(), nullable=True)
    )
    op.add_column("learner_academic_record_terms", _total_units())


def downgrade() -> None:
    """Downgrade schema.

    Dropping these loses the record of how a finalized General Average was
    reached, which cannot be reconstructed afterwards. Push code that no
    longer reads the columns first (docs/operations.md).
    """
    op.drop_column("learner_academic_record_terms", "total_units")
    op.drop_column("learner_academic_record_terms", "averaging_method")
    op.drop_column("learner_academic_record_subjects", "unrounded_final_grade")
    op.drop_column("learner_academic_record_subjects", "units")
    op.drop_column("learner_academic_record_subjects", "units_per_term")
    op.drop_column("learner_academic_records", "total_units")
    op.drop_column("learner_academic_records", "averaging_method")

    for table in ("annual_grade_summaries", "term_grade_summaries"):
        op.drop_column(table, "total_units")
        op.drop_column(table, "averaging_method")

    for table in ("combined_learning_area_results", "subject_final_grades"):
        op.drop_column(table, "unrounded_final_grade")
        op.drop_column(table, "units")
        op.drop_column(table, "units_per_term")

    op.drop_column("grading_policy_versions", "average_from_unrounded_finals")
    op.drop_column("grading_policy_versions", "combine_language_pair_in_term_average")
    op.drop_column("grading_policy_versions", "averaging_method")
    op.drop_constraint(
        "fk_gpv_effective_grade_level_grade_levels",
        "grading_policy_versions",
        type_="foreignkey",
    )
    op.drop_column("grading_policy_versions", "effective_grade_level_id")

    op.drop_column("combined_learning_areas", "units_per_term")
    op.drop_column("section_subject_offerings", "units_per_term")
    op.drop_column("subjects", "instructional_hours_per_year")
    op.drop_column("subjects", "units_per_term")
    op.drop_column("subject_categories", "units_per_term")

    # Last: the type can only go once no column uses it.
    postgresql.ENUM(name="averagingmethod").drop(op.get_bind(), checkfirst=True)
