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
from app.models.enums import OfferingStatus, PolicyVersionStatus


class SubjectCategory(UUIDPKMixin, Base):
    __tablename__ = "subject_categories"

    code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    default_grading_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grading_policies.id", ondelete="RESTRICT")
    )


class GradingPolicy(UUIDPKMixin, Base):
    __tablename__ = "grading_policies"

    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class GradingPolicyVersion(UUIDPKMixin, Base):
    """Versions the passing-grade threshold only — see docs/schema.md §4.

    Grade entry is direct-only (Mode B, confirmed): teachers type the
    official term grade, so no Written Work/Performance Task/Exam weight
    columns or transmutation table exist here.
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
    passing_grade: Mapped[float] = mapped_column(
        Numeric(5, 2), default=75, server_default="75"
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
