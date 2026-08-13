"""The permanent learner academic record (§37, §38).

**This is the system of record for a completed school year, and it is
deliberately built independently of any report layout** — §36 is explicit
that the temporary SF10 is a *report template only*, that its layout may
change, and that "you must never design the database around the visual
coordinates of the temporary SF10". So these tables describe what a
learner achieved, not where it prints; a future SF10 revision reads from
them without any migration or recalculation of grades.

**Everything descriptive is stored as TEXT, not as a foreign key.** That
is the whole point of §38: "if administrators rename a subject or change
a policy in a later school year, historical SF10 records must NOT
change." A row here keeps the subject's name, code and category *as they
were when the year was finalized*. Pointing at `subjects.id` instead
would make a rename in SY 2028-2029 silently rewrite a learner's SY
2026-2027 record — which is exactly the failure this guards against.

The FK columns that remain (`enrollment_id`, `learner_id`, and the
optional id references) exist for lookup and lineage only. Nothing
displayed is ever read back through them.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin
from app.models.enums import CompletionStatus


class LearnerAcademicRecord(UUIDPKMixin, Base):
    """One finalized school year for one learner — the header of the
    permanent record."""

    __tablename__ = "learner_academic_records"
    __table_args__ = (UniqueConstraint("enrollment_id"),)

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    learner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learners.id", ondelete="RESTRICT"), nullable=False
    )

    # --- Frozen identity (§37) -------------------------------------------
    # Copied, not referenced: a learner who marries and changes surname, or
    # a school that updates its name, must not retroactively alter a
    # record already issued.
    lrn: Mapped[str | None] = mapped_column(String)
    learner_name: Mapped[str] = mapped_column(String, nullable=False)
    school_name: Mapped[str | None] = mapped_column(String)
    deped_school_id: Mapped[str | None] = mapped_column(String)
    school_year_name: Mapped[str] = mapped_column(String, nullable=False)
    grade_level: Mapped[str | None] = mapped_column(String)
    section_name: Mapped[str | None] = mapped_column(String)
    track_name: Mapped[str | None] = mapped_column(String)
    strand_name: Mapped[str | None] = mapped_column(String)

    # --- Frozen result ---------------------------------------------------
    general_average: Mapped[float | None] = mapped_column(Numeric(5, 2))
    general_average_remark: Mapped[str | None] = mapped_column(String)
    completion_status: Mapped[CompletionStatus] = mapped_column(
        default=CompletionStatus.INCOMPLETE, server_default=CompletionStatus.INCOMPLETE.value
    )
    award_name: Mapped[str | None] = mapped_column(String)

    # The passing grade actually applied, and which policy version it came
    # from — kept as a number so a later policy edit can't change how a
    # past record reads (§38).
    passing_grade: Mapped[float | None] = mapped_column(Numeric(5, 2))
    grading_policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grading_policy_versions.id", ondelete="SET NULL")
    )
    grading_policy_label: Mapped[str | None] = mapped_column(String)

    snapshot_at: Mapped[datetime] = mapped_column(server_default=func.now())
    snapshot_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # Bumped when an audited reopen leads to the year being finalized
    # again, so a re-issued record is distinguishable from the first.
    revision: Mapped[int] = mapped_column(default=1, server_default="1")


class LearnerAcademicRecordSubject(UUIDPKMixin, Base):
    """One learning area within a finalized year.

    Mirrors what the report shows (§37): subject, category, the three term
    grades, final grade and remark. `is_combined_parent`/`is_component`
    carry the §16 hierarchy so a future template can redraw it without
    re-deriving the rule, and `component_final_grade` preserves the value
    §16 hides on the printed card — the record keeps the truth even where
    the form leaves a blank.
    """

    __tablename__ = "learner_academic_record_subjects"

    learner_academic_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learner_academic_records.id", ondelete="CASCADE"), nullable=False
    )
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # Frozen descriptors — TEXT on purpose (§38).
    subject_name: Mapped[str] = mapped_column(String, nullable=False)
    subject_code: Mapped[str | None] = mapped_column(String)
    subject_category: Mapped[str | None] = mapped_column(String)

    # Lineage only; never read for display.
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="SET NULL")
    )

    # Term applicability (§38) — which terms the subject actually ran in,
    # so a later change to the section's offerings can't make a past
    # record look incomplete.
    offered_term1: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    offered_term2: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    offered_term3: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    term1_grade: Mapped[float | None] = mapped_column(Numeric(5, 2))
    term2_grade: Mapped[float | None] = mapped_column(Numeric(5, 2))
    term3_grade: Mapped[float | None] = mapped_column(Numeric(5, 2))
    final_grade: Mapped[float | None] = mapped_column(Numeric(5, 2))
    remark: Mapped[str | None] = mapped_column(String)

    is_combined_parent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_component: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # A component's own Final Grade, which §16 blanks on the printed card
    # but which the permanent record still needs to hold.
    component_final_grade: Mapped[float | None] = mapped_column(Numeric(5, 2))


class LearnerAcademicRecordTerm(UUIDPKMixin, Base):
    """Per-term averages for a finalized year — what a TERM-scoped award
    was judged on (§17), frozen alongside everything else."""

    __tablename__ = "learner_academic_record_terms"

    learner_academic_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learner_academic_records.id", ondelete="CASCADE"), nullable=False
    )
    term_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    term_name: Mapped[str | None] = mapped_column(String)
    term_average: Mapped[float | None] = mapped_column(Numeric(5, 2))
    completion_status: Mapped[CompletionStatus] = mapped_column(
        default=CompletionStatus.INCOMPLETE, server_default=CompletionStatus.INCOMPLETE.value
    )
    award_name: Mapped[str | None] = mapped_column(String)
    adviser_comment: Mapped[str | None] = mapped_column(String)
