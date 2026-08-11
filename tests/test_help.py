"""The quick guide is only useful if it stays true.

These check the two ways it can silently rot: naming a role that doesn't
exist, and quietly dropping a role that does.
"""

from app.admin_pages import help as help_page
from app.seed import ROLES

SEEDED = {code for code, _ in ROLES}

# Seeded so an administrator can grant them, but no screens are built yet,
# so the guide covers them in the Super Admin section rather than giving
# each its own. Moving one out of here means writing its guide entry.
WITHOUT_SCREENS = {"ATTENDANCE_ENCODER", "SCHOOL_HEAD"}


def test_every_documented_role_actually_exists():
    """A typo here would silently show a section to nobody, since the
    lookup is by role code."""
    assert set(help_page.BY_ROLE) <= SEEDED


def test_every_role_with_screens_is_documented():
    assert SEEDED - set(help_page.BY_ROLE) == WITHOUT_SCREENS


def test_the_guide_warns_that_two_roles_have_no_screens():
    """Granting one of them on its own leaves that person with no pages,
    which is a confusing thing to discover by accident."""
    _, admin_items = help_page.BY_ROLE["SUPER_ADMIN"]
    text = " ".join(body for _, body in admin_items)
    assert "Attendance Encoder" in text and "School Head" in text


def test_no_section_is_empty():
    assert help_page.UNIVERSAL and help_page.NOTES
    for code, (heading, items) in help_page.BY_ROLE.items():
        assert heading and items, code
        for title, body in items:
            assert title.strip() and body.strip(), code


def test_the_two_opposite_language_rules_are_both_explained():
    """§16 collapses the Grade 11 pair into one learning area for the
    annual card; §17 counts them separately for the Term Average. They
    are exact opposites, CLAUDE.md flags the pair as the biggest source of
    bugs, and a parent looking at both documents will notice."""
    notes = " ".join(f"{question} {body}" for question, body in help_page.NOTES)
    assert "Mabisang Komunikasyon" in notes
    assert "**one** learning area" in notes, "the annual rule (§16)"
    assert "two separate subjects" in notes, "the term-card rule (§17)"
