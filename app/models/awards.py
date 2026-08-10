import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin
from app.models.enums import AwardResult, AwardScope, PolicyVersionStatus


class AwardPolicy(UUIDPKMixin, Base):
    __tablename__ = "award_policies"

    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class AwardPolicyVersion(UUIDPKMixin, Base):
    """Two selectable policies from §24 (Academic Excellence, legacy tiered
    Honors) — never permanently merged. `tier_thresholds` carries the
    tiered-Honors structure since tier count/labels vary."""

    __tablename__ = "award_policy_versions"
    __table_args__ = (UniqueConstraint("award_policy_id", "version_number"),)

    award_policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("award_policies.id", ondelete="RESTRICT"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(nullable=False)
    effective_school_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_years.id", ondelete="RESTRICT"), nullable=False
    )
    scope: Mapped[AwardScope] = mapped_column(
        default=AwardScope.ANNUAL, server_default=AwardScope.ANNUAL.value, nullable=False
    )
    require_complete_record: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    require_no_derogatory_record: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    min_general_average: Mapped[float | None] = mapped_column(Numeric(5, 2))
    min_lowest_final_grade: Mapped[float | None] = mapped_column(Numeric(5, 2))
    require_no_failed_subject: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    tier_thresholds: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[PolicyVersionStatus] = mapped_column(
        default=PolicyVersionStatus.DRAFT, server_default=PolicyVersionStatus.DRAFT.value
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default="now()")


class LearnerAward(UUIDPKMixin, Base):
    """Stores the eligibility **reason**, not just a boolean — §24 requires
    showing why a learner isn't eligible.

    `term_id` is NULL for an ANNUAL-scoped policy and set for a
    TERM-scoped one, so a learner can hold up to three Honors rows plus
    one annual Academic Excellence row in the same year. The uniqueness
    rule is therefore "one row per enrollment per policy version per
    term", enforced by two partial indexes rather than one constraint —
    a plain UNIQUE over a nullable column would let duplicate annual rows
    through, since in SQL NULL never equals NULL.
    """

    __tablename__ = "learner_awards"
    __table_args__ = (
        Index(
            "uq_learner_awards_term",
            "enrollment_id",
            "award_policy_version_id",
            "term_id",
            unique=True,
            postgresql_where=text("term_id IS NOT NULL"),
        ),
        Index(
            "uq_learner_awards_annual",
            "enrollment_id",
            "award_policy_version_id",
            unique=True,
            postgresql_where=text("term_id IS NULL"),
        ),
    )

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    school_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_years.id", ondelete="RESTRICT"), nullable=False
    )
    term_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("terms.id", ondelete="RESTRICT")
    )
    award_policy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("award_policy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    award_result: Mapped[AwardResult] = mapped_column(nullable=False)
    award_name: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    is_override: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    override_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    override_reason: Mapped[str | None] = mapped_column(String)
    computed_at: Mapped[datetime] = mapped_column(server_default="now()")
    created_at: Mapped[datetime] = mapped_column(server_default="now()")
