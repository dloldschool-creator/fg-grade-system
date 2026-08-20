import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, VersionMixin
from app.models.enums import AveragingMethod, OfferingStatus, PolicyVersionStatus


class SubjectCategory(UUIDPKMixin, Base):
    __tablename__ = "subject_categories"

    code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    default_grading_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grading_policies.id", ondelete="RESTRICT")
    )
    # DepEd Order 017 s. 2026, Table 19 — the equivalent units one subject in
    # this classification carries **per term**. The broadest level of the unit
    # resolution chain; see `app/curriculum_policy.py:resolve_units`. NULL
    # means "not configured", which weights the subject 1 — i.e. exactly as it
    # counted before units existed — never 0.
    units_per_term: Mapped[float | None] = mapped_column(Numeric(5, 2))


class GradingPolicy(UUIDPKMixin, Base):
    __tablename__ = "grading_policies"

    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class GradingPolicyVersion(UUIDPKMixin, Base):
    """Versions the passing-grade threshold and the averaging rules — see
    docs/schema.md §4.

    Grade entry is direct-only (Mode B, confirmed): teachers type the
    official term grade, so no Written Work/Performance Task/Exam weight
    columns or transmutation table exist here.

    **Averaging is versioned, not global** (CLAUDE.md rule 6). DepEd Order
    017 s. 2026 phases the Strengthened SHS Curriculum in by grade level —
    Grade 11 in SY 2026-2027, Grade 12 not until SY 2027-2028 — so within a
    single school year two different averaging rules are simultaneously
    correct. `effective_grade_level_id` is what lets both exist at once.
    """

    __tablename__ = "grading_policy_versions"
    __table_args__ = (UniqueConstraint("grading_policy_id", "version_number"),)

    grading_policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grading_policies.id", ondelete="RESTRICT"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(nullable=False)
    effective_school_year_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_years.id", ondelete="RESTRICT")
    )
    # NULL = applies to every grade level. Set it to narrow a version to one,
    # which is how SSHS (Grade 11) and the 2016 K to 12 curriculum (Grade 12)
    # coexist in SY 2026-2027.
    effective_grade_level_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # Named explicitly: the project's `fk_%(table)s_%(column)s_%(referred)s`
        # convention would generate a 64-character name here, one over
        # Postgres's 63-byte identifier limit, and Postgres truncates rather
        # than complaining — leaving Alembic autogenerate to "fix" the name on
        # every future run.
        ForeignKey(
            "grade_levels.id",
            ondelete="RESTRICT",
            name="fk_gpv_effective_grade_level_grade_levels",
        ),
    )
    passing_grade: Mapped[float] = mapped_column(
        Numeric(5, 2), default=75, server_default="75"
    )

    # --- Averaging rules (DO 017 s. 2026, Annex E) -----------------------
    # Every one of these defaults to the pre-DO-017 behaviour, so a policy
    # version that says nothing computes exactly what the app computed before
    # the columns existed. Nothing changes until a version opts in.
    averaging_method: Mapped[AveragingMethod] = mapped_column(
        default=AveragingMethod.UNWEIGHTED, server_default=AveragingMethod.UNWEIGHTED.value
    )
    # DO 017 Table 1 makes Effective Communication / Mabisang Komunikasyon one
    # 160-hour core subject, and Annex E prints it as a single row with one
    # grade per term. master-spec §17 says the opposite — count the two
    # components separately in the Term Average. Both readings are defensible,
    # and which applies is the school's call (and its SDO's), so it is stored
    # rather than decided in code. False keeps §17.
    combine_language_pair_in_term_average: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # DO 017's worked examples feed the General Average from **unrounded**
    # subject finals (78.666…, not the 78 they print). Its own annex is not
    # self-consistent about which it shows, so this too is stored rather than
    # assumed. False keeps the rounded whole number the app has always used.
    average_from_unrounded_finals: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    min_grade: Mapped[float] = mapped_column(Numeric(5, 2), default=60, server_default="60")
    max_grade: Mapped[float] = mapped_column(Numeric(5, 2), default=100, server_default="100")
    status: Mapped[PolicyVersionStatus] = mapped_column(
        default=PolicyVersionStatus.DRAFT, server_default=PolicyVersionStatus.DRAFT.value
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Subject(UUIDPKMixin, TimestampMixin, Base):
    """Immutable-ID catalog; names are never used as keys (§8)."""

    __tablename__ = "subjects"

    code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    official_name: Mapped[str] = mapped_column(String, nullable=False)
    short_name: Mapped[str] = mapped_column(String, nullable=False)
    grade_level_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grade_levels.id", ondelete="RESTRICT"), nullable=False
    )
    subject_category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subject_categories.id", ondelete="RESTRICT"), nullable=False
    )
    track_restriction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tracks.id", ondelete="RESTRICT")
    )
    default_grading_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grading_policies.id", ondelete="RESTRICT")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    archived_at: Mapped[datetime | None]

    # Overrides the category's units_per_term. This is how one category can
    # carry two values: DO 017 Table 19 gives a TechPro elective 4 units in
    # Grade 11 (320 hours across 3 terms) and 12 in Grade 12 (320 hours in a
    # single term), and both are `TECHPRO_ELECTIVE`.
    units_per_term: Mapped[float | None] = mapped_column(Numeric(5, 2))
    # The prescribed hours DO 017 assigns the subject. Not read at runtime —
    # it is what `grading_engine.units_from_hours` derives units from when
    # seeding, and the audit trail for why a unit value is what it is.
    instructional_hours_per_year: Mapped[int | None] = mapped_column(SmallInteger)


class CombinedLearningArea(UUIDPKMixin, Base):
    """The Grade 11 Effective Communication / Mabisang Komunikasyon parent
    virtual learning area, modeled as data (§14-16, §62)."""

    __tablename__ = "combined_learning_areas"

    name: Mapped[str] = mapped_column(String, nullable=False)
    grade_level_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grade_levels.id", ondelete="RESTRICT"), nullable=False
    )
    display_order: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # The units the pair carries as ONE learning area. DO 017 makes it a
    # single 2-unit core subject, so this is 2 — deliberately not the sum of
    # the two components, which would double-weight the languages. NULL falls
    # back to one component's units, the same thing whenever the components
    # are configured alike.
    units_per_term: Mapped[float | None] = mapped_column(Numeric(5, 2))


class CombinedLearningAreaComponent(UUIDPKMixin, Base):
    __tablename__ = "combined_learning_area_components"

    combined_learning_area_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("combined_learning_areas.id", ondelete="RESTRICT"),
        nullable=False,
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    display_order: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")


class SubjectProfile(UUIDPKMixin, TimestampMixin, Base):
    """Per-track/strand default subject sets (§7, §12-13) — seed data only;
    `section_subject_offerings` is the actual per-section source of truth (§48)."""

    __tablename__ = "subject_profiles"

    name: Mapped[str] = mapped_column(String, nullable=False)
    grade_level_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grade_levels.id", ondelete="RESTRICT"), nullable=False
    )
    track_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tracks.id", ondelete="RESTRICT"), nullable=False
    )
    strand_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strands.id", ondelete="RESTRICT"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class SubjectProfileSubject(UUIDPKMixin, Base):
    __tablename__ = "subject_profile_subjects"
    __table_args__ = (UniqueConstraint("subject_profile_id", "subject_id"),)

    subject_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subject_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT"), nullable=False
    )
    term1_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    term2_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    term3_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_elective: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    display_order: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")


class SectionSubjectOffering(UUIDPKMixin, TimestampMixin, VersionMixin, Base):
    """**Single source of truth** for what a learner is actually graded on (§48)."""

    __tablename__ = "section_subject_offerings"
    __table_args__ = (UniqueConstraint("section_id", "subject_id", "term_id"),)

    school_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_years.id", ondelete="RESTRICT"), nullable=False
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id", ondelete="RESTRICT"), nullable=False
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="RESTRICT"), nullable=False
    )
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("terms.id", ondelete="RESTRICT"), nullable=False
    )
    subject_category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subject_categories.id", ondelete="RESTRICT"), nullable=False
    )
    grading_policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grading_policy_versions.id", ondelete="RESTRICT")
    )
    is_required: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    display_order: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    status: Mapped[OfferingStatus] = mapped_column(
        default=OfferingStatus.PLACEHOLDER, server_default=OfferingStatus.PLACEHOLDER.value
    )
    # Narrowest level of the unit resolution chain — set only when this
    # section teaches the subject at hours the catalog entry doesn't
    # describe. Normally NULL.
    units_per_term: Mapped[float | None] = mapped_column(Numeric(5, 2))


class TeacherAssignment(UUIDPKMixin, Base):
    """References the offering rather than embedding a teacher column
    directly on it, so reassignment is itself an auditable event (§47)."""

    __tablename__ = "teacher_assignments"
    __table_args__ = (
        Index(
            "uq_teacher_assignments_active_offering",
            "section_subject_offering_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    section_subject_offering_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("section_subject_offerings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    teacher_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime] = mapped_column(server_default=func.now())
    unassigned_at: Mapped[datetime | None]
