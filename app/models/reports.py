import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin
from app.models.enums import ReportReadiness, ReportType


class ReportTemplate(UUIDPKMixin, Base):
    """Versioned by type/effective date so the DB never needs
    restructuring when a DepEd form layout changes (§56). `field_mapping`
    maps stored fields to printable cell positions — see sf-templates/ for
    the real layouts (SF9, SF2 so far; SF10 to be added once available)."""

    __tablename__ = "report_templates"
    __table_args__ = (UniqueConstraint("report_type", "version_number"),)

    report_type: Mapped[ReportType] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    version_number: Mapped[int] = mapped_column(nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    template_file_path: Mapped[str] = mapped_column(String, nullable=False)
    field_mapping: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ReportGenerationLog(UUIDPKMixin, Base):
    __tablename__ = "report_generation_logs"

    report_type: Mapped[ReportType] = mapped_column(nullable=False)
    report_template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("report_templates.id", ondelete="RESTRICT"), nullable=False
    )
    generated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False)
    file_path: Mapped[str | None] = mapped_column(String)
    readiness_status: Mapped[ReportReadiness] = mapped_column(nullable=False)
    generated_at: Mapped[datetime] = mapped_column(server_default=func.now())


# The deferred `report_snapshots` idea is now realised as the permanent
# academic record in app/models/academic_record.py — see §38. It freezes
# the learner's *result* rather than a rendered file, which is what makes
# a later template revision reprintable without recalculating grades.
