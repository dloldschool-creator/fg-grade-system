"""The importable kinds (§51) — learners and term grades.

Each spec supplies its columns, a validator and a committer; the sequence
around them lives in `app/import_pipeline.py`.

Both validators load their reference data **once** for the whole file.
The database is ~85ms away, so checking each row with its own query would
make a 300-row masterlist take half a minute.

§51 names the errors these have to catch: duplicate LRN, unknown section,
unknown subject, invalid grade, impossible date, and a subject not
offered during that term. Each has a test.
"""

from app.import_pipeline import (
    ColumnSpec,
    ImportSpec,
    RowError,
    ValidationResult,
    parse_date,
    parse_grade,
    parse_lrn,
)
from app.models.academic_structure import Section
from app.models.enums import EnrollmentStatus, GradeWorkflowStatus, ImportJobType, Sex
from app.models.grades import TermGrade
from app.models.learners import Enrollment, Learner
from app.models.organization import Term
from app.models.subjects import SectionSubjectOffering, Subject
from app.naming import normalize_name

# --- Learners --------------------------------------------------------------

LEARNER_COLUMNS = [
    ColumnSpec("last_name", "Last Name", True, ("surname", "familyname")),
    ColumnSpec("first_name", "First Name", True, ("givenname",)),
    ColumnSpec("middle_name", "Middle Name", False, ("middlename",)),
    ColumnSpec("extension_name", "Extension Name", False, ("suffix", "extname")),
    ColumnSpec("sex", "Sex", True, ("gender",)),
    ColumnSpec("birthdate", "Birthdate", True, ("dateofbirth", "dob", "birthday")),
    ColumnSpec("lrn", "LRN", False, ("learnerreferencenumber",)),
    # Optional. Filled in, the learner is enrolled into that section in
    # one step; left blank, they are created and enrolled later on the
    # Enrollment page, which is what this import did before.
    ColumnSpec("section", "Section", False, ("sectionname", "class")),
]


def _parse_sex(raw: str):
    value = str(raw or "").strip().upper()
    if value in ("M", "MALE"):
        return Sex.MALE, None
    if value in ("F", "FEMALE"):
        return Sex.FEMALE, None
    return None, f"{raw!r} is not MALE or FEMALE"


def _section_lookup(session, school_year_id) -> tuple[dict, set]:
    """Sections for the year, keyed by upper-cased name.

    A name is only unique per grade level, so the same name may legally
    exist in both Grade 11 and Grade 12. Those names are collected
    separately and rejected rather than guessed at — silently enrolling a
    Grade 11 learner into the Grade 12 section of the same name would be
    very hard to notice afterwards.
    """
    by_name: dict[str, object] = {}
    ambiguous: set[str] = set()
    if school_year_id is None:
        return by_name, ambiguous
    for section in session.query(Section).filter_by(school_year_id=school_year_id).all():
        key = (section.name or "").strip().upper()
        if key in by_name:
            ambiguous.add(key)
        by_name[key] = section
    return by_name, ambiguous


def validate_learners(
    session, rows: list[dict], mapping: dict, *, school_year_id=None
) -> ValidationResult:
    result = ValidationResult()

    existing_lrns = {
        lrn for (lrn,) in session.query(Learner.lrn).filter(Learner.lrn.isnot(None)).all()
    }
    seen_in_file: dict[str, int] = {}
    # Loaded once for the whole file, not per row — a 1,200-row masterlist
    # checked one query at a time would take minutes.
    sections_by_name, ambiguous_names = _section_lookup(session, school_year_id)

    for row in rows:
        number = row.get("__row__")
        errors_before = len(result.errors)

        last_name = normalize_name(row.get("last_name"))
        first_name = normalize_name(row.get("first_name"))
        if not last_name:
            result.errors.append(RowError(number, "Last Name", "required"))
        if not first_name:
            result.errors.append(RowError(number, "First Name", "required"))

        sex, sex_error = _parse_sex(row.get("sex"))
        if sex_error:
            result.errors.append(RowError(number, "Sex", sex_error))

        birthdate, date_error = parse_date(row.get("birthdate"))
        if date_error:
            result.errors.append(RowError(number, "Birthdate", date_error))
        elif birthdate is None:
            result.errors.append(RowError(number, "Birthdate", "required"))

        lrn, lrn_error = parse_lrn(row.get("lrn"))
        if lrn_error:
            result.errors.append(RowError(number, "LRN", lrn_error))
        elif lrn:
            if lrn in existing_lrns:
                result.errors.append(
                    RowError(number, "LRN", f"duplicate LRN — {lrn} already exists in the system")
                )
            elif lrn in seen_in_file:
                result.errors.append(
                    RowError(
                        number, "LRN",
                        f"duplicate LRN — same as row {seen_in_file[lrn]} in this file",
                    )
                )
            else:
                seen_in_file[lrn] = number

        # Optional. Left blank, the learner is created but not enrolled —
        # exactly what the import did before this column existed.
        section = None
        raw_section = (row.get("section") or "").strip()
        if raw_section:
            if school_year_id is None:
                result.errors.append(
                    RowError(number, "Section", "choose a school year above to enrol into a section")
                )
            elif raw_section.upper() in ambiguous_names:
                result.errors.append(
                    RowError(
                        number, "Section",
                        f"more than one section is called {raw_section!r} this school year — "
                        "rename one of them so they can be told apart",
                    )
                )
            else:
                section = sections_by_name.get(raw_section.upper())
                if section is None:
                    result.errors.append(
                        RowError(number, "Section", f"unknown section {raw_section!r}")
                    )

        if len(result.errors) == errors_before:
            result.parsed.append(
                {
                    "__row__": number,
                    "last_name": last_name,
                    "first_name": first_name,
                    "middle_name": normalize_name(row.get("middle_name")),
                    "extension_name": normalize_name(row.get("extension_name")),
                    "sex": sex,
                    "birthdate": birthdate,
                    "lrn": lrn,
                    "section_id": section.id if section else None,
                    "grade_level_id": section.grade_level_id if section else None,
                    "school_year_id": school_year_id if section else None,
                }
            )
    return result


def commit_learners(session, parsed: list[dict], user_id=None) -> int:
    for row in parsed:
        learner = Learner(
            last_name=row["last_name"],
            first_name=row["first_name"],
            middle_name=row["middle_name"],
            extension_name=row["extension_name"],
            sex=row["sex"],
            birthdate=row["birthdate"],
            lrn=row["lrn"],
        )
        session.add(learner)

        if not row.get("section_id"):
            continue
        # This codebase declares no ORM relationship() between models, so
        # SQLAlchemy cannot infer that the learner must be inserted before
        # the enrollment that references it by a bare FK column. Flushing
        # here makes the generated id available and fixes the insert order.
        session.flush()
        session.add(
            Enrollment(
                learner_id=learner.id,
                school_year_id=row["school_year_id"],
                grade_level_id=row["grade_level_id"],
                section_id=row["section_id"],
                enrollment_status=EnrollmentStatus.ENROLLED,
            )
        )
    return len(parsed)


# --- Term grades -----------------------------------------------------------

TERM_GRADE_COLUMNS = [
    ColumnSpec("lrn", "LRN", True, ("learnerreferencenumber",)),
    ColumnSpec("section", "Section", True, ("sectionname",)),
    ColumnSpec("subject", "Subject", True, ("subjectname", "learningarea")),
    ColumnSpec("term", "Term", True, ("termnumber", "quarter")),
    ColumnSpec("grade", "Grade", True, ("finalgrade", "rating", "termgrade")),
]


def _parse_term_number(raw: str):
    value = str(raw or "").strip().upper()
    for prefix in ("TERM", "T", "QUARTER", "Q"):
        if value.startswith(prefix):
            value = value[len(prefix):].strip()
            break
    if value in ("1", "2", "3"):
        return int(value), None
    return None, f"{raw!r} is not term 1, 2 or 3"


def validate_term_grades(session, rows: list[dict], mapping: dict) -> ValidationResult:
    """Resolves each row against the section's actual offerings.

    The last check is the one that matters most: a grade for a subject
    the section doesn't offer *in that term* is rejected rather than
    silently written, because `section_subject_offerings` is the single
    source of truth for what a learner is graded on (rule 5).
    """
    result = ValidationResult()

    learners_by_lrn = {
        lrn: learner_id
        for learner_id, lrn in session.query(Learner.id, Learner.lrn)
        .filter(Learner.lrn.isnot(None))
        .all()
    }
    sections = {s.name.strip().upper(): s for s in session.query(Section).all()}
    subjects = {s.official_name.strip().upper(): s for s in session.query(Subject).all()}
    # (section_id, subject_id, term_number) -> offering
    offerings: dict = {}
    terms = {t.id: t for t in session.query(Term).all()}
    for offering in session.query(SectionSubjectOffering).all():
        term = terms.get(offering.term_id)
        if term:
            offerings[(offering.section_id, offering.subject_id, term.term_number)] = offering
    enrollments = {
        (e.learner_id, e.section_id): e for e in session.query(Enrollment).all()
    }
    existing = {
        (g.enrollment_id, g.section_subject_offering_id): g
        for g in session.query(TermGrade).all()
    }

    for row in rows:
        number = row.get("__row__")
        errors_before = len(result.errors)

        lrn, lrn_error = parse_lrn(row.get("lrn"))
        if lrn_error:
            result.errors.append(RowError(number, "LRN", lrn_error))
        elif not lrn:
            result.errors.append(RowError(number, "LRN", "required"))
        elif lrn not in learners_by_lrn:
            result.errors.append(RowError(number, "LRN", f"no learner with LRN {lrn}"))

        section = sections.get(str(row.get("section", "")).strip().upper())
        if section is None:
            result.errors.append(
                RowError(number, "Section", f"unknown section {row.get('section')!r}")
            )

        subject = subjects.get(str(row.get("subject", "")).strip().upper())
        if subject is None:
            result.errors.append(
                RowError(number, "Subject", f"unknown subject {row.get('subject')!r}")
            )

        term_number, term_error = _parse_term_number(row.get("term"))
        if term_error:
            result.errors.append(RowError(number, "Term", term_error))

        grade, grade_error = parse_grade(row.get("grade"))
        if grade_error:
            result.errors.append(RowError(number, "Grade", f"invalid grade — {grade_error}"))
        elif grade is None:
            result.errors.append(RowError(number, "Grade", "required"))

        if len(result.errors) != errors_before:
            continue

        enrollment = enrollments.get((learners_by_lrn[lrn], section.id))
        if enrollment is None:
            result.errors.append(
                RowError(number, "Section", f"learner {lrn} is not enrolled in {section.name}")
            )
            continue

        offering = offerings.get((section.id, subject.id, term_number))
        if offering is None:
            result.errors.append(
                RowError(
                    number, "Subject",
                    f"{subject.official_name} is not offered to {section.name} in term "
                    f"{term_number}",
                )
            )
            continue

        result.parsed.append(
            {
                "__row__": number,
                "enrollment_id": enrollment.id,
                "offering_id": offering.id,
                "term_id": offering.term_id,
                "grade": grade,
                "replaces_existing": (enrollment.id, offering.id) in existing,
            }
        )
    return result


def commit_term_grades(session, parsed: list[dict], user_id=None) -> int:
    """Writes as DRAFT, never straight to FINALIZED — a migrated grade
    still goes through the normal submit/verify workflow (rule 7)."""
    existing = {
        (g.enrollment_id, g.section_subject_offering_id): g
        for g in session.query(TermGrade).all()
    }
    for row in parsed:
        key = (row["enrollment_id"], row["offering_id"])
        grade = existing.get(key)
        if grade is None:
            session.add(
                TermGrade(
                    enrollment_id=row["enrollment_id"],
                    section_subject_offering_id=row["offering_id"],
                    term_id=row["term_id"],
                    official_grade=row["grade"],
                    encoded_by_user_id=user_id,
                    status=GradeWorkflowStatus.DRAFT,
                )
            )
        else:
            grade.official_grade = row["grade"]
            grade.encoded_by_user_id = user_id
            grade.status = GradeWorkflowStatus.DRAFT
            grade.version = (grade.version or 0) + 1
    return len(parsed)


LEARNER_IMPORT = ImportSpec(
    job_type=ImportJobType.LEARNERS,
    label="Learners",
    description=(
        "The learner masterlist. Fill in the optional Section column and each "
        "learner is enrolled at the same time; leave it blank and they are "
        "created only, to be enrolled later on the Enrollment page."
    ),
    columns=LEARNER_COLUMNS,
    validate=validate_learners,
    commit=commit_learners,
    needs_school_year=True,
)

TERM_GRADE_IMPORT = ImportSpec(
    job_type=ImportJobType.TERM_GRADES,
    label="Term grades",
    description=(
        "One row per learner, subject and term. Grades land as DRAFT and still "
        "go through the normal submit/verify workflow. Re-importing the same "
        "learner/subject/term updates the existing grade rather than duplicating it."
    ),
    columns=TERM_GRADE_COLUMNS,
    validate=validate_term_grades,
    commit=commit_term_grades,
)

SPECS = {spec.job_type: spec for spec in (LEARNER_IMPORT, TERM_GRADE_IMPORT)}
