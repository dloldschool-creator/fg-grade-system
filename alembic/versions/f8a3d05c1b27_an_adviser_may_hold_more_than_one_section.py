"""An adviser may hold more than one section in a school year

Drops ``uq_sections_adviser_per_school_year``.

The school runs SNED sections — 4 in Grade 11, 3 in Grade 12 — and one
Grade 11 adviser holds two of them: same strand, same subjects, same
room, 5 and 7 learners. The index refused it.

**The rule it enforced had no source.** The migration that added it
(``14e55ba4624b``) is bare autogenerate, "please adjust!" comment
included, and the model comment above the index explained only why it
was scoped per year and made partial — never why one section per adviser
should be true. Meanwhile §3C of the specification says an adviser sees
learners in "assigned **sections**", plural, and every adviser lookup in
the application is a ``filter_by`` returning a list: ``section_picker``
already renders a *dropdown* of an adviser's sections, and
``delete_user`` already reports "still advises N section(s)". Nothing
anywhere calls ``.one()`` on it. The application was written for this;
one index was not.

Nothing else about two sections is a problem, which was checked before
dropping this rather than after: awards are computed from thresholds on
the general average, not ranked within a section, so a learner's result
is the same in a section of 5, 7 or 12; and
``uq_teacher_assignments_active_offering`` constrains one active teacher
*per offering*, not one offering per teacher, so the same subject teacher
covers both sections without hitting a second wall.

Data only in effect — an index is removed, no row is touched, and no
query depends on it existing. The declaration in ``__table_args__`` is
DDL-time only, so the running app is unaffected either side of this and
it can be applied before or after the code deploy.

Revision ID: f8a3d05c1b27
Revises: e2b6c1f4a733
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from alembic import op

revision = "f8a3d05c1b27"
down_revision = "e2b6c1f4a733"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "uq_sections_adviser_per_school_year",
        table_name="sections",
        postgresql_where=sa.text("adviser_user_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Will fail if any adviser has since been given a second section,
    # which is correct: silently picking one to strip is worse than
    # refusing, and the person restoring the rule needs to know it is
    # being applied to data that breaks it.
    op.create_index(
        "uq_sections_adviser_per_school_year",
        "sections",
        ["school_year_id", "adviser_user_id"],
        unique=True,
        postgresql_where=sa.text("adviser_user_id IS NOT NULL"),
    )
