"""The quick guide is only useful if it stays true.

These check the two ways it can silently rot: naming a role that doesn't
exist, and quietly dropping a role that does.
"""

from app.admin_pages import help as help_page
from app.seed import ROLES

SEEDED = {code for code, _ in ROLES}

# Seeded so an administrator can grant it, but no screens are built yet,
# so the guide covers it in the Super Admin section rather than giving it
# its own. Moving it out of here means writing its guide entry.
WITHOUT_SCREENS = {"ATTENDANCE_ENCODER"}


def test_every_documented_role_actually_exists():
    """A typo here would silently show a section to nobody, since the
    lookup is by role code."""
    assert set(help_page.BY_ROLE) <= SEEDED


def test_every_role_with_screens_is_documented():
    assert SEEDED - set(help_page.BY_ROLE) == WITHOUT_SCREENS


def test_the_guide_warns_about_the_role_with_no_screens():
    """Granting it on its own leaves that person with no pages, which is a
    confusing thing to discover by accident."""
    _, admin_items = help_page.BY_ROLE["SUPER_ADMIN"]
    text = " ".join(body for _, body in admin_items)
    for code in WITHOUT_SCREENS:
        assert code.replace("_", " ").title() in text, code


def test_no_section_is_empty():
    assert help_page.UNIVERSAL and help_page.NOTES
    for code, (heading, items) in help_page.BY_ROLE.items():
        assert heading and items, code
        for title, body in items:
            assert title.strip() and body.strip(), code


def test_the_notes_heading_agrees_with_how_many_there_are():
    """Trivial, but the heading is written out in full rather than
    pluralised at runtime, so it silently goes wrong the moment a note is
    added or removed."""
    import inspect

    source = inspect.getsource(help_page.render)
    expected = "Question that comes up" if len(help_page.NOTES) == 1 else "Questions that come up"
    assert expected in source
