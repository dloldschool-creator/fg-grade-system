"""Tests for the migration tooling (§51 import, §52 export).

The reading, mapping and value-parsing halves need no database. The
validators do, so those tests run against a real session and roll back.
"""

import io
import zipfile
from datetime import date
from decimal import Decimal

import openpyxl
import pytest

from app.database import SessionLocal
from app.export_service import ExportTable, to_csv, to_xlsx
from app.import_pipeline import (
    apply_mapping,
    missing_required,
    parse_date,
    parse_grade,
    parse_lrn,
    read_table,
    suggest_mapping,
)
from app.import_specs import (
    LEARNER_IMPORT,
    TERM_GRADE_IMPORT,
    validate_learners,
    validate_term_grades,
)
from app.models.learners import Learner


@pytest.fixture
def session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# --- Reading (§51 steps 1-2) -----------------------------------------------


def test_csv_is_read_with_row_numbers_matching_the_spreadsheet():
    """Errors are reported by row number, so it has to be the number the
    user sees — 1 is the header, so data starts at 2."""
    data = b"Last Name,First Name\nDela Cruz,Juan\nSantos,Maria\n"
    headers, rows = read_table(data, "masterlist.csv")
    assert headers == ["Last Name", "First Name"]
    assert [r["__row__"] for r in rows] == [2, 3]


def test_lrn_keeps_its_leading_zero_when_read():
    """Rule 10: an LRN is an identifier, not a number."""
    _, rows = read_table(b"LRN\n012345678901\n", "x.csv")
    assert rows[0]["LRN"] == "012345678901"


def test_excel_numeric_lrn_is_not_read_in_scientific_notation():
    """Excel hands a 12-digit LRN back as a float; a naive str() would
    render 1.07041140016e+11 and destroy the value."""
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(["LRN"])
    worksheet.append([107041140016])
    buffer = io.BytesIO()
    workbook.save(buffer)

    _, rows = read_table(buffer.getvalue(), "x.xlsx")
    assert rows[0]["LRN"] == "107041140016"
    assert "e+" not in rows[0]["LRN"]


def test_blank_rows_are_skipped():
    _, rows = read_table(b"A,B\n1,2\n,\n3,4\n", "x.csv")
    assert len(rows) == 2


def test_excel_dates_come_through_as_iso_strings():
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(["Birthdate"])
    worksheet.append([date(2009, 1, 15)])
    buffer = io.BytesIO()
    workbook.save(buffer)
    _, rows = read_table(buffer.getvalue(), "x.xlsx")
    assert rows[0]["Birthdate"] == "2009-01-15"


# --- Mapping (§51 step 3) --------------------------------------------------


def test_mapping_is_suggested_from_real_world_header_spellings():
    headers = ["Surname", "Given Name", "Gender", "Date of Birth", "Learner Reference Number"]
    mapping = suggest_mapping(headers, LEARNER_IMPORT)
    assert mapping["last_name"] == "Surname"
    assert mapping["first_name"] == "Given Name"
    assert mapping["sex"] == "Gender"
    assert mapping["birthdate"] == "Date of Birth"
    assert mapping["lrn"] == "Learner Reference Number"


def test_mapping_ignores_case_and_punctuation():
    mapping = suggest_mapping(["LAST_NAME", "first name"], LEARNER_IMPORT)
    assert mapping["last_name"] == "LAST_NAME"
    assert mapping["first_name"] == "first name"


def test_missing_required_columns_are_reported_by_label():
    absent = missing_required({"last_name": "Surname"}, LEARNER_IMPORT)
    assert "First Name" in absent
    assert "Last Name" not in absent
    assert "Middle Name" not in absent  # optional


def test_apply_mapping_preserves_the_row_number():
    rows = [{"Surname": "Dela Cruz", "__row__": 7}]
    mapped = apply_mapping(rows, {"last_name": "Surname"})
    assert mapped == [{"last_name": "Dela Cruz", "__row__": 7}]


# --- Value parsing ---------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [("90", 90), ("90.4", 90), ("90.6", 91), ("", None)])
def test_grades_parse_and_round(raw, expected):
    value, error = parse_grade(raw)
    assert error is None
    assert value == expected


def test_a_blank_grade_is_not_a_zero():
    """Rule 2 at the import boundary: blank means not yet encoded."""
    assert parse_grade("") == (None, None)


@pytest.mark.parametrize("raw", ["abc", "101", "-5"])
def test_invalid_grades_are_reported(raw):
    value, error = parse_grade(raw)
    assert value is None and error


@pytest.mark.parametrize(
    "raw, expected",
    [("2009-01-15", date(2009, 1, 15)), ("15/01/2009", date(2009, 1, 15))],
)
def test_dates_parse_in_common_formats(raw, expected):
    assert parse_date(raw) == (expected, None)


@pytest.mark.parametrize("raw", ["2009-13-01", "not a date", "31/02/2009"])
def test_impossible_dates_are_reported(raw):
    """§51 names "impossible date" as a validation error."""
    value, error = parse_date(raw)
    assert value is None and error


@pytest.mark.parametrize("raw", ["12345", "abcdefghijkl", "1234567890123"])
def test_malformed_lrns_are_reported(raw):
    value, error = parse_lrn(raw)
    assert value is None and error


def test_valid_lrn_stays_a_string():
    value, error = parse_lrn("012345678901")
    assert error is None
    assert value == "012345678901"
    assert isinstance(value, str)


# --- Validation against the database (§51 steps 4-5) -----------------------


def _learner_rows(**overrides):
    row = {
        "__row__": 2,
        "last_name": "Testcase",
        "first_name": "Import",
        "middle_name": "",
        "extension_name": "",
        "sex": "MALE",
        "birthdate": "2009-01-15",
        "lrn": "",
        "section": "",
    }
    row.update(overrides)
    return [row]


def test_duplicate_lrn_against_the_database_is_rejected(session):
    """§51's first named error."""
    existing = session.query(Learner).filter(Learner.lrn.isnot(None)).first()
    if existing is None:
        pytest.skip("no learner with an LRN to collide with")
    result = validate_learners(session, _learner_rows(lrn=existing.lrn), {})
    assert not result.ok
    assert any("duplicate LRN" in e.message for e in result.errors)


def test_duplicate_lrn_within_the_same_file_is_rejected(session):
    rows = _learner_rows(lrn="099999999901")
    second = dict(rows[0])
    second["__row__"] = 3
    result = validate_learners(session, rows + [second], {})
    assert not result.ok
    assert any("row 2" in e.message for e in result.errors)


def test_a_valid_learner_row_parses_and_is_uppercased(session):
    result = validate_learners(session, _learner_rows(last_name="dela cruz"), {})
    assert result.ok, result.error_dicts()
    assert result.parsed[0]["last_name"] == "DELA CRUZ"


def test_learner_row_missing_required_fields_is_rejected(session):
    result = validate_learners(session, _learner_rows(last_name="", birthdate=""), {})
    assert not result.ok
    assert {e.column for e in result.errors} >= {"Last Name", "Birthdate"}


def test_unknown_section_and_subject_are_rejected(session):
    """Two more of §51's named errors."""
    rows = [
        {
            "__row__": 2,
            "lrn": "012345678901",
            "section": "NO SUCH SECTION",
            "subject": "NO SUCH SUBJECT",
            "term": "1",
            "grade": "90",
        }
    ]
    result = validate_term_grades(session, rows, {})
    assert not result.ok
    messages = " ".join(e.message for e in result.errors)
    assert "unknown section" in messages
    assert "unknown subject" in messages


def test_term_grades_reject_a_subject_not_offered_that_term(session):
    """The §51 error that matters most, because section_subject_offerings
    is the single source of truth for what a learner is graded on
    (rule 5). A grade for a term the subject doesn't run in must not be
    silently written."""
    from app.models.academic_structure import Section
    from app.models.organization import Term
    from app.models.subjects import SectionSubjectOffering, Subject

    from app.models.learners import Enrollment

    # Find a subject that genuinely doesn't run in every term — a
    # term-specific elective. Taking the first offering would often land
    # on a full-year subject and skip the very case being tested.
    terms = {t.id: t.term_number for t in session.query(Term).all()}
    by_pair: dict = {}
    for row in session.query(SectionSubjectOffering).all():
        term_number = terms.get(row.term_id)
        if term_number:
            by_pair.setdefault((row.section_id, row.subject_id), set()).add(term_number)

    candidate = next(
        ((pair, {1, 2, 3} - offered) for pair, offered in by_pair.items() if {1, 2, 3} - offered),
        None,
    )
    if candidate is None:
        pytest.skip("every configured subject runs in all three terms")
    (section_id, subject_id), unoffered = candidate
    section = session.get(Section, section_id)
    subject = session.get(Subject, subject_id)

    learner = (
        session.query(Learner)
        .join(Enrollment, Enrollment.learner_id == Learner.id)
        .filter(Enrollment.section_id == section_id, Learner.lrn.isnot(None))
        .first()
    )
    if learner is None:
        pytest.skip("no enrolled learner with an LRN in that section")

    rows = [
        {
            "__row__": 2,
            "lrn": learner.lrn,
            "section": section.name,
            "subject": subject.official_name,
            "term": str(sorted(unoffered)[0]),
            "grade": "90",
        }
    ]
    result = validate_term_grades(session, rows, {})
    assert not result.ok
    assert any("is not offered" in e.message for e in result.errors)


# --- Enrolling from the learner import -------------------------------------


def test_a_blank_section_creates_the_learner_without_enrolling(session):
    """The column is optional, and leaving it blank must behave exactly as
    the import did before it existed."""
    result = validate_learners(session, _learner_rows(section=""), {}, school_year_id=None)
    assert result.ok, result.error_dicts()
    assert result.parsed[0]["section_id"] is None


def test_an_unknown_section_is_rejected(session):
    from app.models.organization import SchoolYear

    school_year = session.query(SchoolYear).first()
    if school_year is None:
        pytest.skip("no school year")
    result = validate_learners(
        session, _learner_rows(section="NO SUCH SECTION"), {}, school_year_id=school_year.id
    )
    assert not result.ok
    assert any("unknown section" in e.message for e in result.errors)


def test_a_section_without_a_school_year_is_rejected(session):
    """The school year is chosen on the page, not repeated on 1,200 rows.
    A file naming a section with no year selected cannot be resolved, and
    guessing which year to enrol into would be worse than refusing."""
    result = validate_learners(
        session, _learner_rows(section="ANY"), {}, school_year_id=None
    )
    assert not result.ok
    assert any("school year" in e.message for e in result.errors)


def test_a_real_section_carries_its_grade_level(session):
    """The grade level comes from the section rather than the file, so the
    two can never disagree."""
    from app.models.academic_structure import Section
    from app.models.organization import SchoolYear

    school_year = session.query(SchoolYear).first()
    if school_year is None:
        pytest.skip("no school year")
    section = session.query(Section).filter_by(school_year_id=school_year.id).first()
    if section is None:
        pytest.skip("no sections")

    result = validate_learners(
        session, _learner_rows(section=section.name), {}, school_year_id=school_year.id
    )
    assert result.ok, result.error_dicts()
    row = result.parsed[0]
    assert row["section_id"] == section.id
    assert row["grade_level_id"] == section.grade_level_id
    assert row["school_year_id"] == school_year.id


def test_the_section_column_is_optional_and_auto_detected():
    from app.import_pipeline import suggest_mapping

    column = next(c for c in LEARNER_IMPORT.columns if c.field == "section")
    assert not column.required
    for header in ("Section", "Section Name", "Class"):
        assert suggest_mapping([header], LEARNER_IMPORT).get("section") == header


# --- Export (§52) ----------------------------------------------------------


def _table() -> ExportTable:
    return ExportTable(
        "Masterlist",
        ["LRN", "Name", "Grade"],
        [
            {"LRN": "012345678901", "Name": "DELA CRUZ, JUAN", "Grade": 90},
            {"LRN": "107041140016", "Name": "SANTOS, MARIA", "Grade": None},
        ],
    )


def test_xlsx_export_writes_lrn_as_text_with_its_leading_zero():
    """§52: "Exports should preserve LRN as text." Written as a number,
    Excel shows 1.2345678901E+11 and drops the leading zero."""
    workbook = openpyxl.load_workbook(io.BytesIO(to_xlsx(_table())))
    worksheet = workbook.active
    cell = worksheet["A2"]
    assert cell.value == "012345678901"
    assert isinstance(cell.value, str)
    assert cell.number_format == "@"  # text, so Excel won't re-coerce on save


def test_xlsx_export_is_a_real_workbook_with_a_header_row():
    data = to_xlsx(_table())
    assert zipfile.ZipFile(io.BytesIO(data)).namelist()  # valid xlsx container
    worksheet = openpyxl.load_workbook(io.BytesIO(data)).active
    assert [c.value for c in worksheet[1]] == ["LRN", "Name", "Grade"]
    assert worksheet.freeze_panes == "A2"


def test_csv_export_quotes_every_field_so_lrn_survives():
    text = to_csv(_table()).decode("utf-8-sig")
    assert '"012345678901"' in text
    lines = text.strip().splitlines()
    assert lines[0] == '"LRN","Name","Grade"'


def test_csv_export_writes_a_missing_grade_as_blank_not_zero():
    text = to_csv(_table()).decode("utf-8-sig")
    assert text.strip().splitlines()[2].endswith('""')
