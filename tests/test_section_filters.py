"""The strand filter must offer only strands the chosen grade level runs.

Nine pages pick a section through `section_picker`, so the filters above
that dropdown are one implementation shared by all of them. Offering every
strand in the school regardless of grade level is not a crash — it is a
dropdown whose entries silently resolve to "No sections match those
filters", which reads as missing data rather than an impossible
combination.

`picker_options` is pure so the rule is testable without a database or a
Streamlit run; the widget wiring around it is not, and is checked by
reading the page instead (see `test_page_text.py`).
"""

from types import SimpleNamespace

from app.admin_pages._helpers import ALL, picker_options

G11, G12 = "g11", "g12"


def grade(id_):
    return SimpleNamespace(id=id_)


def strand(id_):
    return SimpleNamespace(id=id_)


def section(grade_level_id, strand_id):
    return SimpleNamespace(grade_level_id=grade_level_id, strand_id=strand_id)


GRADES = [grade(G11), grade(G12)]
STRANDS = [strand("abm"), strand("humss"), strand("css")]

# CSS is a TechPro strand the school only runs in Grade 12.
SECTIONS = [
    section(G11, "abm"),
    section(G11, "humss"),
    section(G12, "abm"),
    section(G12, "css"),
]


# --- The cascade ----------------------------------------------------------


def test_strands_narrow_to_the_chosen_grade_level():
    _, strands = picker_options(SECTIONS, GRADES, STRANDS, G11)

    assert [s.id for s in strands] == ["abm", "humss"]


def test_the_other_grade_level_offers_its_own_strands():
    _, strands = picker_options(SECTIONS, GRADES, STRANDS, G12)

    assert [s.id for s in strands] == ["abm", "css"]


def test_all_grade_levels_offers_every_strand_in_use():
    _, strands = picker_options(SECTIONS, GRADES, STRANDS, ALL)

    assert [s.id for s in strands] == ["abm", "humss", "css"]


def test_a_strand_no_section_uses_is_never_offered():
    """Strands come from the Strand table, which lists what the school
    could run — not what it does run this year."""
    _, strands = picker_options(SECTIONS, GRADES, STRANDS + [strand("stem")], ALL)

    assert "stem" not in [s.id for s in strands]


def test_grade_levels_do_not_narrow_themselves():
    """Only the strand list cascades. Narrowing the grade dropdown to the
    grade already chosen would leave no way back to the other one."""
    grades, _ = picker_options(SECTIONS, GRADES, STRANDS, G11)

    assert [g.id for g in grades] == [G11, G12]


# --- Choices that no longer exist -----------------------------------------


def test_a_grade_level_not_in_the_list_falls_back_to_all():
    """What a selection left over from another school year looks like.
    Narrowing to nothing would hide every section behind a filter the user
    cannot see they still have set."""
    grades, strands = picker_options(SECTIONS, GRADES, STRANDS, "g10-from-last-year")

    assert [g.id for g in grades] == [G11, G12]
    assert [s.id for s in strands] == ["abm", "humss", "css"]


def test_a_grade_level_with_no_sections_is_not_offered():
    empty = [section(G12, "abm")]
    grades, _ = picker_options(empty, GRADES, STRANDS, ALL)

    assert [g.id for g in grades] == [G12]


# --- Sections with no strand ----------------------------------------------


def test_a_section_without_a_strand_does_not_invent_one():
    """`sections.strand_id` is nullable — a section can be created before
    its strand is decided, and None must not match a real strand."""
    _, strands = picker_options([section(G11, None)], GRADES, STRANDS, G11)

    assert strands == []
