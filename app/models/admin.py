import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin
from app.models.enums import ExportJobStatus, ImportJobStatus, ImportJobType


class AuditLog(UUIDPKMixin, Base):
    """Append-only; the app layer must never expose an update/delete path
    for this table (§50)."""

    __tablename__ = "audit_logs"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    object_type: Mapped[str] = mapped_column(String, nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    previous_value: Mapped[dict | None] = mapped_column(JSONB)
    new_value: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(String)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(server_default="now()")


class ImportJob(UUIDPKMixin, Base):
    """Preview -> validate -> confirm workflow (§51) — never a silent import."""

    __tablename__ = "import_jobs"

    job_type: Mapped[ImportJobType] = mapped_column(nullable=False)
    status: Mapped[ImportJobStatus] = mapped_column(
        default=ImportJobStatus.UPLOADED, server_default=ImportJobStatus.UPLOADED.value
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    column_mapping: Mapped[dict | None] = mapped_column(JSONB)
    validation_errors: Mapped[dict | None] = mapped_column(JSONB)
    row_count: Mapped[int | None] = mapped_column(Integer)
    imported_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(server_default="now()")
    confirmed_at: Mapped[datetime | None]


class ExportJob(UUIDPKMixin, Base):
    __tablename__ = "export_jobs"

    export_type: Mapped[str] = mapped_column(String, nullable=False)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False)
    file_path: Mapped[str | None] = mapped_column(String)
    status: Mapped[ExportJobStatus] = mapped_column(
        default=ExportJobStatus.PENDING, server_default=ExportJobStatus.PENDING.value
    )
    created_at: Mapped[datetime] = mapped_column(server_default="now()")
    completed_at: Mapped[datetime | None]
