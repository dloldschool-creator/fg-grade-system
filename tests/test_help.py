"""The quick guide is only useful if it stays true.

These check the two ways it can silently rot: naming a role that doesn't
exist, and quietly dropping a role that does.
"""

from app.admin_pages import help as help_page
from app.seed import ROLES

SEEDED = {code for code, _ in ROLES}


def test_every_documented_role_actually_exists():
    """A typo here would silently show a section to nobody, since the
    lookup is by role code."""
    assert set(help_page.BY_ROLE) <= SEEDED


def test_every_role_with_screens_is_documented():
    """Every seeded role now has screens and a guide section.

    This used to allow one exception, ATTENDANCE_ENCODER, which was
    seeded so it could be granted while nothing was ever built for it.
    It was removed on 2026-08-16 — advisers encode their own section's
    attendance — so the exception is gone and a role added without a
    guide entry fails here. Grant-a-role-that-does-nothing was the state
    this test was tolerating; it should not come back silently.
    """
    assert SEEDED == set(help_page.BY_ROLE)


def test_no_section_is_empty():
    assert help_page.UNIVERSAL
    for code, (heading, items) in help_page.BY_ROLE.items():
        assert heading and items, code
        for title, body in items:
            assert title.strip() and body.strip(), code
