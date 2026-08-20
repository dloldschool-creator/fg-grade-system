"""Section Subject Offerings must default to *this* section's profile.

**The trap.** `subject_profiles` is keyed by (grade level, track, strand)
and has no section column, so every section in a strand matches the same
set. The picker listed them unordered with no `index=`, so it defaulted to
whichever row Postgres returned first — for MUSK that was
`G12-TECHPRO-CSS-JOBS`, while the offerings list underneath, queried by
`section_id`, was correct. Reported from the live app on 2026-08-20; nine
sections were exposed, six of them defaulting to another section's profile.

It is not cosmetic. Profiles in one strand differ by subject *and by term*:
the two CSS profiles run the same three subjects in swapped terms, and the
Kitchen Operations ones differ in the subject itself (Kitchen Operations vs
a 12-unit Work Immersion). Seeding from the wrong one puts real subjects in
the wrong terms, and rule 4 builds the General Average from each subject's
real term pattern.

`profile_for_section` is pure, so most of this needs no database.
"""

from types import SimpleNamespace

import pytest

from app.admin_pages.section_offerings import profile_for_section


def _section(name):
    return SimpleNamespace(name=name)


def _profiles(*names):
    return [SimpleNamespace(name=n) for n in names]


# --- the reported bug -----------------------------------------------------


def test_musk_gets_musks_profile_not_jobs():
    profiles = _profiles("G12-TECHPRO-CSS-JOBS", "G12-TECHPRO-CSS-MUSK")
    assert profile_for_section(_section("MUSK"), profiles).name == "G12-TECHPRO-CSS-MUSK"


def test_jobs_still_gets_its_own():
    profiles = _profiles("G12-TECHPRO-CSS-JOBS", "G12-TECHPRO-CSS-MUSK")
    assert profile_for_section(_section("JOBS"), profiles).name == "G12-TECHPRO-CSS-JOBS"


@pytest.mark.parametrize(
    "section", ["ADRIA", "BOURDAIN", "DUCASSE", "RAMSAY"]
)
def test_every_kitchen_operations_section_gets_its_own(section):
    """Four sections, four profiles, two different subjects between them."""
    profiles = _profiles(
        "G12-TECHPRO-KO-ADRIA",
        "G12-TECHPRO-KO-BOURDAIN",
        "G12-TECHPRO-KO-DUCASSE",
        "G12-TECHPRO-KO-RAMSAY",
    )
    assert profile_for_section(_section(section), profiles).name.endswith(section)


# --- when the convention can't answer, it must say so ---------------------


def test_no_match_returns_none_rather_than_guessing():
    profiles = _profiles("G12-TECHPRO-CSS-JOBS", "G12-TECHPRO-CSS-MUSK")
    assert profile_for_section(_section("BEZOS"), profiles) is None


def test_an_ambiguous_suffix_returns_none():
    """Two profiles ending the same way is not a match — picking either
    would be the guess this function exists to avoid."""
    profiles = _profiles("G12-TECHPRO-CSS-MUSK", "G12-TECHPRO-KO-MUSK")
    assert profile_for_section(_section("MUSK"), profiles) is None


def test_a_substring_is_not_a_suffix():
    """MUS must not match MUSK — the separator is part of the convention."""
    profiles = _profiles("G12-TECHPRO-CSS-MUSK")
    assert profile_for_section(_section("MUS"), profiles) is None


def test_matching_ignores_case_and_padding():
    profiles = _profiles("G12-TechPro-CSS-Musk")
    assert profile_for_section(_section("  musk "), profiles) is not None


def test_the_only_profile_still_needs_to_be_named_for_the_section():
    """Deliberate: a single match is handled by the caller, which has the
    list length to reason about. This function answers one question only —
    is there a profile that says it belongs to this section."""
    assert profile_for_section(_section("BEZOS"), _profiles("G11-ACAD-STEM")) is None


# --- against the real database -------------------------------------------


def test_every_live_section_resolves_or_is_unambiguous():
    """No section in the database may be left picking between profiles with
    no default — that is the state that produced the bug."""
    from app.database import SessionLocal
    from app.models.academic_structure import Section
    from app.models.subjects import SubjectProfile

    session = SessionLocal()
    try:
        stranded = []
        for section in session.query(Section).all():
            profiles = (
                session.query(SubjectProfile)
                .filter_by(
                    grade_level_id=section.grade_level_id,
                    track_id=section.track_id,
                    strand_id=section.strand_id,
                )
                .order_by(SubjectProfile.name)
                .all()
            )
            if len(profiles) > 1 and profile_for_section(section, profiles) is None:
                stranded.append(f"{section.name}: {[p.name for p in profiles]}")
        assert not stranded, (
            "These sections match several profiles and none is named for them, "
            "so the picker can only ask. Rename the profile to end in the "
            "section's name:\n  " + "\n  ".join(stranded)
        )
    finally:
        session.rollback()
        session.close()
