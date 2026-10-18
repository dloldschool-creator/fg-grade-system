"""SHS admission record upload (Learner Masterlist), matched by LRN.

Every database test writes, flushes, asserts and rolls back; nothing here
commits. Learners are constructed with an LRN no real learner has (the
`lrn` column is unique, and the prefix keeps clear of the school's range).
"""

import io
import random
import uuid
from datetime import date

import openpyxl
import pytest
from sqlalchemy import event

from app.database import engine, SessionLocal
from app.import_pipeline import apply_mapping, read_table, suggest_mapping
from app.import_specs import (
    ADMISSION_COLUMNS,
    GRADE_12_MESSAGE,
    LEARNER_ADMISSION_IMPORT,
    admission_template_bytes,
    commit_learner_admissions,
    validate_learner_admissions,
)
from app.models.academic_structure import GradeLevel, Section
from app.models.admin import AuditLog
from app.models.enums import EnrollmentStatus, Sex
from app.models.learners import Enrollment, Learner, LearnerAdmissionRecord
from app.text_rules import ADDRESS_MAX, NOTE_MAX


@pytest.fixture
def session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _lrn() -> str:
    return "99" + "".join(random.choice("0123456789") for _ in range(10))


def _learner(session, **overrides) -> Learner:
    values = dict(
        lrn=_lrn(), last_name="ZZTEST", first_name="ADMISSION",
        sex=Sex.MALE, birthdate=date(2010, 1, 2),
    )
    values.update(overrides)
    learner = Learner(**values)
    session.add(learner)
    session.flush()
    return learner


def _validate(session, rows, **kwargs):
    for number, row in enumerate(rows, start=2):
        row.setdefault("__row__", number)
    return validate_learner_admissions(session, rows, {}, **kwargs)


def _errors(result):
    return [(e.column, e.message) for e in result.errors]


# --- Matching and required column ------------------------------------------


def test_only_the_lrn_column_is_required():
    assert [c.field for c in ADMISSION_COLUMNS if c.required] == ["lrn"]


def test_every_form_field_has_a_column():
    fields = {c.field for c in ADMISSION_COLUMNS}
    assert fields >= {
        "date_of_shs_admission", "high_school_completer", "high_school_general_average",
        "high_school_completion_date", "junior_high_school_completer",
        "junior_high_school_general_average", "previous_school_name",
        "previous_school_address", "pept_passer", "pept_rating", "pept_examination_date",
        "als_ae_passer", "als_ae_rating", "als_ae_examination_date", "clc_name",
        "clc_address", "other_eligibility_notes",
    }


def test_the_templates_own_headers_map_to_every_column():
    headers, rows = read_table(admission_template_bytes(), "t.xlsx")
    mapping = suggest_mapping(headers, LEARNER_ADMISSION_IMPORT)
    assert set(mapping) == {c.field for c in ADMISSION_COLUMNS}
    assert len(rows) == 1


def test_the_template_example_row_is_refused_not_written(session):
    headers, rows = read_table(admission_template_bytes(), "t.xlsx")
    mapped = apply_mapping(rows, suggest_mapping(headers, LEARNER_ADMISSION_IMPORT))
    result = validate_learner_admissions(session, mapped, {})
    assert not result.parsed
    assert "no learner with LRN" in result.errors[0].message


@pytest.mark.parametrize("raw, fragment", [("", "required"), ("123", "12 digits")])
def test_a_bad_or_missing_lrn_is_a_row_error(session, raw, fragment):
    result = _validate(session, [{"lrn": raw, "pept_passer": "Yes"}])
    assert fragment in result.errors[0].message


def test_an_unknown_lrn_and_a_repeated_lrn_are_row_errors(session):
    learner = _learner(session)
    result = _validate(
        session,
        [
            {"lrn": learner.lrn, "pept_passer": "Yes"},
            {"lrn": learner.lrn, "pept_passer": "No"},
            {"lrn": "988888888888", "pept_passer": "No"},
        ],
    )
    messages = [e.message for e in result.errors]
    assert any("same LRN as row 2" in m for m in messages)
    assert any("no learner with LRN" in m for m in messages)
    assert len(result.parsed) == 1


# --- Value rules ----------------------------------------------------------


@pytest.mark.parametrize("raw", ["Yes", "yes", "YES", "True", "1", "Y"])
def test_yes_in_any_case(session, raw):
    learner = _learner(session)
    result = _validate(session, [{"lrn": learner.lrn, "pept_passer": raw}])
    assert result.parsed[0]["pept_passer"] is True


@pytest.mark.parametrize("raw", ["No", "no", "NO", "False", "0", "N"])
def test_no_in_any_case_is_false_not_blank(session, raw):
    learner = _learner(session)
    result = _validate(session, [{"lrn": learner.lrn, "pept_passer": raw}])
    assert result.parsed[0]["pept_passer"] is False


def test_a_boolean_that_is_neither_is_an_error(session):
    learner = _learner(session)
    result = _validate(session, [{"lrn": learner.lrn, "als_ae_passer": "maybe"}])
    assert _errors(result) == [("ALS A&E Passer", "'maybe' is not Yes or No")]


@pytest.mark.parametrize("raw", ["-1", "100.5", "abc", "nan", "inf"])
def test_ratings_and_averages_must_be_0_to_100(session, raw):
    learner = _learner(session)
    for field in ("pept_rating", "als_ae_rating", "junior_high_school_general_average",
                  "high_school_general_average"):
        result = _validate(session, [{"lrn": learner.lrn, field: raw}])
        assert result.errors and not result.parsed, (field, raw)


def test_the_limits_zero_and_a_hundred_are_accepted(session):
    learner = _learner(session)
    result = _validate(
        session, [{"lrn": learner.lrn, "pept_rating": "0", "als_ae_rating": "100"}]
    )
    assert not result.errors
    assert str(result.parsed[0]["als_ae_rating"]) == "100.00"


def test_an_impossible_date_is_an_error_and_iso_is_read(session):
    learner = _learner(session)
    bad = _validate(session, [{"lrn": learner.lrn, "pept_examination_date": "2026-02-30"}])
    assert bad.errors and not bad.parsed
    ok = _validate(session, [{"lrn": learner.lrn, "pept_examination_date": "2026-02-27"}])
    assert ok.parsed[0]["pept_examination_date"] == date(2026, 2, 27)


@pytest.mark.parametrize(
    "field, limit",
    [
        ("previous_school_name", ADDRESS_MAX), ("previous_school_address", ADDRESS_MAX),
        ("clc_name", ADDRESS_MAX), ("clc_address", ADDRESS_MAX),
        ("other_eligibility_notes", NOTE_MAX),
    ],
)
def test_over_long_text_is_refused_and_the_limit_itself_is_fine(session, field, limit):
    learner = _learner(session)
    over = _validate(session, [{"lrn": learner.lrn, field: "x" * (limit + 1)}])
    assert over.errors and "longer than" in over.errors[0].message
    assert not over.parsed
    exact = _validate(session, [{"lrn": learner.lrn, field: "x" * limit}])
    assert not exact.errors


def test_a_row_with_only_an_lrn_is_skipped_with_a_warning(session):
    learner = _learner(session)
    result = _validate(session, [{"lrn": learner.lrn}])
    assert not result.parsed and not result.errors
    assert len(result.warnings) == 1


def test_one_bad_cell_keeps_the_whole_row_out(session):
    learner = _learner(session)
    result = _validate(
        session, [{"lrn": learner.lrn, "pept_passer": "Yes", "pept_rating": "500"}]
    )
    assert not result.parsed


# --- Scope: Grade 12 and adviser -------------------------------------------


def _grade_12_section(session):
    for section, level in (
        session.query(Section, GradeLevel).join(GradeLevel, GradeLevel.id == Section.grade_level_id).all()
    ):
        if "".join(ch for ch in (level.code or level.name or "") if ch.isdigit()) == "12":
            return section
    pytest.skip("no Grade 12 section in the database")


def test_a_grade_12_learner_is_refused_with_the_message(session):
    section = _grade_12_section(session)
    learner = _learner(session)
    session.add(
        Enrollment(
            learner_id=learner.id,
            school_year_id=section.school_year_id,
            grade_level_id=section.grade_level_id,
            section_id=section.id,
            enrollment_status=EnrollmentStatus.ENROLLED,
        )
    )
    session.flush()
    result = _validate(session, [{"lrn": learner.lrn, "pept_passer": "Yes"}])
    assert _errors(result) == [("LRN", GRADE_12_MESSAGE)]
    assert not result.parsed


def test_a_learner_enrolled_nowhere_is_accepted(session):
    learner = _learner(session)
    result = _validate(session, [{"lrn": learner.lrn, "pept_passer": "Yes"}])
    assert len(result.parsed) == 1


def test_an_adviser_cannot_touch_a_learner_outside_their_set(session):
    mine, theirs = _learner(session), _learner(session)
    result = _validate(
        session,
        [
            {"lrn": mine.lrn, "pept_passer": "Yes"},
            {"lrn": theirs.lrn, "pept_passer": "Yes"},
        ],
        adviser_user_id=str(uuid.uuid4()),
        editable_ids={mine.id},
    )
    assert [p["lrn"] for p in result.parsed] == [mine.lrn]
    assert _errors(result) == [("LRN", f"{theirs.lrn} is not one of your learners")]


def test_a_registrar_is_unscoped(session):
    learner = _learner(session)
    result = _validate(
        session, [{"lrn": learner.lrn, "pept_passer": "Yes"}], adviser_user_id=None
    )
    assert len(result.parsed) == 1


# --- Commit ----------------------------------------------------------------


def _commit(session, rows, **kwargs):
    result = _validate(session, rows, **kwargs)
    assert not result.errors, _errors(result)
    return commit_learner_admissions(session, result.parsed, None)


def _record(session, learner):
    session.flush()
    return session.query(LearnerAdmissionRecord).filter_by(learner_id=learner.id).one()


def test_a_missing_record_is_created(session):
    learner = _learner(session)
    assert _commit(session, [{"lrn": learner.lrn, "previous_school_name": "Old High"}]) == 1
    assert _record(session, learner).previous_school_name == "Old High"


def test_a_blank_cell_keeps_the_stored_value_and_a_filled_one_overwrites(session):
    learner = _learner(session)
    session.add(
        LearnerAdmissionRecord(
            learner_id=learner.id, previous_school_name="Old High", pept_passer=True,
            pept_rating=70, clc_name="Keep me",
        )
    )
    session.flush()
    _commit(
        session,
        [{"lrn": learner.lrn, "previous_school_name": "", "pept_passer": "", "clc_name": "New CLC",
          "als_ae_rating": "81.5"}],
    )
    record = _record(session, learner)
    assert record.previous_school_name == "Old High"
    assert record.pept_passer is True
    assert float(record.pept_rating) == 70
    assert record.clc_name == "New CLC"
    assert float(record.als_ae_rating) == 81.5


def test_no_overwrites_a_stored_yes(session):
    learner = _learner(session)
    session.add(LearnerAdmissionRecord(learner_id=learner.id, pept_passer=True))
    session.flush()
    _commit(session, [{"lrn": learner.lrn, "pept_passer": "No"}])
    assert _record(session, learner).pept_passer is False


def test_yes_no_and_a_real_excel_date_round_trip_through_an_xlsx(session):
    learner = _learner(session)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["LRN", "Date of SHS Admission", "JHS Completer", "HS Completer",
                  "JHS General Average"])
    sheet.append([learner.lrn, date(2026, 6, 8), "yes", "NO", 88.5])
    buffer = io.BytesIO()
    workbook.save(buffer)

    headers, rows = read_table(buffer.getvalue(), "f.xlsx")
    mapped = apply_mapping(rows, suggest_mapping(headers, LEARNER_ADMISSION_IMPORT))
    result = validate_learner_admissions(session, mapped, {})
    assert not result.errors, _errors(result)
    assert commit_learner_admissions(session, result.parsed, None) == 1

    record = _record(session, learner)
    assert record.date_of_shs_admission == date(2026, 6, 8)
    assert record.junior_high_school_completer is True
    assert record.high_school_completer is False
    assert float(record.junior_high_school_general_average) == 88.5


def test_the_lrn_survives_as_text_with_a_leading_zero(session):
    learner = _learner(session, lrn="012345678901")
    headers, rows = read_table(
        b"LRN,PEPT Passer\n012345678901,Yes\n", "f.csv"
    )
    mapped = apply_mapping(rows, suggest_mapping(headers, LEARNER_ADMISSION_IMPORT))
    result = validate_learner_admissions(session, mapped, {})
    assert result.parsed[0]["lrn"] == "012345678901"
    assert result.parsed[0]["learner_id"] == learner.id


def _audit(session, learner):
    session.flush()
    return (
        session.query(AuditLog)
        .filter_by(action="LEARNER_ADMISSION_CHANGED", object_id=learner.id)
        .all()
    )


def test_a_change_is_audited_with_old_and_new_and_a_repeat_is_not(session):
    learner = _learner(session)
    session.add(LearnerAdmissionRecord(learner_id=learner.id, previous_school_name="Old High"))
    session.flush()
    row = {"lrn": learner.lrn, "previous_school_name": "New High", "pept_passer": "Yes"}

    assert _commit(session, [dict(row)]) == 1
    entries = _audit(session, learner)
    assert len(entries) == 1
    assert entries[0].previous_value["previous_school_name"] == "Old High"
    assert entries[0].new_value["previous_school_name"] == "New High"
    assert entries[0].new_value["created"] is False

    assert _commit(session, [dict(row)]) == 0
    assert len(_audit(session, learner)) == 1


# --- Cost ---------------------------------------------------------------------


class _Counter:
    def __init__(self):
        self.count = 0

    def __enter__(self):
        event.listen(engine, "before_cursor_execute", self._hit)
        return self

    def __exit__(self, *exc):
        event.remove(engine, "before_cursor_execute", self._hit)

    def _hit(self, *args):
        self.count += 1


def _cost(session, size):
    learners = [_learner(session) for _ in range(size)]
    session.flush()
    session.expire_all()
    rows = [
        {"lrn": learner.lrn, "pept_passer": "Yes", "clc_name": "CLC", "__row__": i + 2}
        for i, learner in enumerate(learners)
    ]
    with _Counter() as counter:
        result = validate_learner_admissions(session, rows, {})
        commit_learner_admissions(session, result.parsed, None)
    assert len(result.parsed) == size
    return counter.count


def test_validate_and_commit_cost_the_same_for_2_rows_as_for_30(session):
    """Round trips are the cost (~85 ms each). Counting includes the
    autoflush of the new rows, which is a single INSERT batch."""
    small = _cost(session, 2)
    large = _cost(session, 30)
    session.rollback()
    assert large <= small + 2, (small, large)
    assert large <= 12, large
