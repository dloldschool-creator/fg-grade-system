"""Every generated .xlsx must be a package Excel will actually open.

The bug this exists for: SF4 downloaded fine, was a valid ZIP, and every
XML part inside it parsed — but Excel offered to "recover" it instead of
opening it.

The cause was a dangling relationship. **openpyxl cannot round-trip an
externalLinks part.** Our templates are print-view sheets pulled from the
school's master workbook, so each one carries a link to it; the templates
are valid (externalBook references rId1/rId2/rId3, and the .rels declares
all three), but on save openpyxl writes `externalBook r:id="rId1"` while
numbering the surviving relationship rId3. Excel follows rId1, finds
nothing, and calls the file damaged.

SF2 and SF9 already dropped the link before saving. SF4 was added later
and the step was missed — nothing failed, and the file looked right until
someone double-clicked it.

So this checks the shipped bytes rather than the objects: no externalLinks
part at all, and no `r:id` anywhere in the package that its own .rels
doesn't declare. The second half is the general form of the bug and would
catch a broken image or drawing reference too.
"""

import io
import re
import zipfile

import pytest

R_ID = re.compile(rb'r:(?:id|embed|link)="([^"]+)"')
DECLARED_ID = re.compile(rb'Id="([^"]+)"')


def _rels_name(part: str) -> str:
    head, _, tail = part.rpartition("/")
    return f"{head}/_rels/{tail}.rels" if head else f"_rels/{tail}.rels"


def dangling_references(data: bytes) -> list[str]:
    """Every relationship id a part points at that its .rels never declares."""
    problems = []
    archive = zipfile.ZipFile(io.BytesIO(data))
    names = set(archive.namelist())
    for part in names:
        if not part.endswith(".xml") or "/_rels/" in part:
            continue
        used = set(R_ID.findall(archive.read(part)))
        if not used:
            continue
        rels = _rels_name(part)
        declared = set(DECLARED_ID.findall(archive.read(rels))) if rels in names else set()
        for missing in sorted(used - declared):
            problems.append(f"{part} references {missing.decode()} but {rels} does not declare it")
    return problems


def external_link_parts(data: bytes) -> list[str]:
    archive = zipfile.ZipFile(io.BytesIO(data))
    return [n for n in archive.namelist() if n.startswith("xl/externalLinks/")]


# --- The generated forms ---------------------------------------------------


def _sf4_bytes(session):
    from app.excel_template import workbook_to_bytes
    from app.models.organization import SchoolYear
    from app.sf4_report import build_sf4_workbook

    year = session.query(SchoolYear).first()
    if year is None:
        pytest.skip("no school year configured")
    return workbook_to_bytes(build_sf4_workbook(session, year.id, 2026, 6))


def _sf2_bytes(session):
    from app.excel_template import workbook_to_bytes
    from app.models.academic_structure import Section
    from app.models.organization import SchoolYear
    from app.sf2_report import build_sf2_workbook

    year = session.query(SchoolYear).first()
    section = session.query(Section).filter_by(school_year_id=year.id).first() if year else None
    if section is None:
        pytest.skip("no section configured")
    return workbook_to_bytes(build_sf2_workbook(session, section.id, year.id, 2026, 6))


def _sf9_bytes(session):
    from app.excel_template import workbook_to_bytes
    from app.models.learners import Enrollment
    from app.sf9_report import build_sf9_workbook

    enrollment = session.query(Enrollment).first()
    if enrollment is None:
        pytest.skip("no enrolled learner")
    return workbook_to_bytes(build_sf9_workbook(session, enrollment.id))


# SF2 and SF9 were already correct; they are here so they stay that way.
BUILDERS = {"SF2": _sf2_bytes, "SF4": _sf4_bytes, "SF9": _sf9_bytes}


@pytest.fixture
def session():
    from app.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_the_generated_workbook_has_no_dangling_relationships(session, name):
    problems = dangling_references(BUILDERS[name](session))
    assert not problems, f"{name} would open as damaged:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_the_link_to_the_master_workbook_is_gone(session, name):
    """Not just cosmetic — see the module docstring. It is also what stops
    the file prompting to update links against a workbook the teacher
    receiving it has never had."""
    parts = external_link_parts(BUILDERS[name](session))
    assert not parts, f"{name} still ships {parts}"


# --- The detector itself ---------------------------------------------------


def test_the_check_catches_the_bug_it_was_written_for():
    """A structural test that cannot fail is worthless. This rebuilds the
    exact defect — externalBook pointing at rId1, .rels declaring rId3 —
    and asserts the detector reports it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/externalLinks/externalLink1.xml",
            b'<externalLink xmlns:r="http://x"><externalBook r:id="rId1"/></externalLink>',
        )
        archive.writestr(
            "xl/externalLinks/_rels/externalLink1.xml.rels",
            b'<Relationships><Relationship Id="rId3" Target="../../Master.xlsx"/></Relationships>',
        )
    problems = dangling_references(buffer.getvalue())
    assert len(problems) == 1
    assert "rId1" in problems[0]


def test_a_matching_reference_is_not_reported():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            b'<worksheet xmlns:r="http://x"><drawing r:id="rId1"/></worksheet>',
        )
        archive.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            b'<Relationships><Relationship Id="rId1" Target="../drawings/drawing1.xml"/></Relationships>',
        )
    assert dangling_references(buffer.getvalue()) == []
