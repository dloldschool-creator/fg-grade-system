import enum


class SchoolYearStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class GradeEncodingStatus(str, enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"


class FinalizationState(str, enum.Enum):
    """Shared by terms.finalization_state and attendance_month_status.status (§33)."""

    NOT_STARTED = "NOT_STARTED"
    OPEN = "OPEN"
    FOR_REVIEW = "FOR_REVIEW"
    FINALIZED = "FINALIZED"


class PolicyVersionStatus(str, enum.Enum):
    """Shared by grading_policy_versions.status and award_policy_versions.status."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class OfferingStatus(str, enum.Enum):
    PLACEHOLDER = "PLACEHOLDER"
    CONFIRMED = "CONFIRMED"


class Sex(str, enum.Enum):
    MALE = "MALE"
    FEMALE = "FEMALE"


class EnrollmentStatus(str, enum.Enum):
    """Shared by enrollments.enrollment_status and learner_movements.movement_type (§27, §32)."""

    ENROLLED = "ENROLLED"
    LATE_ENROLLMENT = "LATE_ENROLLMENT"
    TRANSFERRED_IN = "TRANSFERRED_IN"
    TRANSFERRED_OUT = "TRANSFERRED_OUT"
    NLS = "NLS"
    DROPPED = "DROPPED"
    SHIFTED_IN = "SHIFTED_IN"
    SHIFTED_OUT = "SHIFTED_OUT"
    COMPLETED = "COMPLETED"
    GRADUATED = "GRADUATED"
    OTHER = "OTHER"


class GradeWorkflowStatus(str, enum.Enum):
    """§45."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    VERIFIED = "VERIFIED"
    FINALIZED = "FINALIZED"


class SubjectRemark(str, enum.Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCOMPLETE = "INCOMPLETE"


class CompletionStatus(str, enum.Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class FinalizationScopeType(str, enum.Enum):
    TERM_SECTION_SUBJECT = "TERM_SECTION_SUBJECT"
    ANNUAL_ENROLLMENT = "ANNUAL_ENROLLMENT"
    ATTENDANCE_MONTH = "ATTENDANCE_MONTH"


class FinalizationRecordStatus(str, enum.Enum):
    FINALIZED = "FINALIZED"
    REOPENED = "REOPENED"


class AttendanceStatus(str, enum.Enum):
    """Internal codes — SF2 renderer maps to printed abbreviations (§30)."""

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LATE = "LATE"
    CUTTING = "CUTTING"


class AwardScope(str, enum.Enum):
    """What a given award policy is evaluated against.

    TERM — judged per term against that term's Term Average (§17), so a
    learner can earn it up to three times a year. This is the legacy
    tiered Honors shape.
    ANNUAL — judged once against the year's General Average (§19/§20),
    which is what the Academic Excellence Award (§24) uses.
    """

    TERM = "TERM"
    ANNUAL = "ANNUAL"


class AwardResult(str, enum.Enum):
    ELIGIBLE_AWARDED = "ELIGIBLE_AWARDED"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


class ReportType(str, enum.Enum):
    SF2 = "SF2"
    SF9_G11 = "SF9_G11"
    SF9_G12 = "SF9_G12"
    SF10 = "SF10"
    TERM_CARD = "TERM_CARD"
    CERTIFICATE = "CERTIFICATE"


class ReportReadiness(str, enum.Enum):
    READY = "READY"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"


class ImportJobType(str, enum.Enum):
    LEARNERS = "LEARNERS"
    SUBJECT_CATALOG = "SUBJECT_CATALOG"
    SUBJECT_PROFILES = "SUBJECT_PROFILES"
    TERM_GRADES = "TERM_GRADES"
    ATTENDANCE = "ATTENDANCE"
    SCHOOL_INFO = "SCHOOL_INFO"


class ImportJobStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


class ExportJobStatus(str, enum.Enum):
    PENDING = "PENDING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
