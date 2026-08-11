"""The Gradebook's submission-deadline warning.

`terms.submission_deadline` gates nothing — encoding is controlled only
by the OPEN/CLOSED toggle — so this must inform without blocking. The
boundary is the part worth pinning: being late by a day is different from
being on time, and a term with no deadline set must not warn at all.
"""

from datetime import date

import pytest

from app.admin_pages.gradebook import days_past_deadline

DEADLINE = date(2026, 9, 15)


def test_nothing_is_late_on_the_deadline_itself():
    """A deadline is a date, not an instant — grades submitted on the day
    are on time."""
    assert days_past_deadline(DEADLINE, today=DEADLINE) is None


def test_the_day_before_is_not_late():
    assert days_past_deadline(DEADLINE, today=date(2026, 9, 14)) is None


def test_the_day_after_is_one_day_late():
    assert days_past_deadline(DEADLINE, today=date(2026, 9, 16)) == 1


def test_lateness_counts_in_days():
    assert days_past_deadline(DEADLINE, today=date(2026, 10, 1)) == 16


def test_a_term_with_no_deadline_never_warns():
    """The column is nullable and the seeded terms may leave it unset —
    an absent deadline is not an overdue one."""
    assert days_past_deadline(None, today=date(2027, 1, 1)) is None


def test_the_banner_does_not_block_encoding():
    """The warning is advisory. If it ever gained a `return` or a
    `st.stop()`, a late teacher would be locked out of the very work they
    are late with — and nothing else in the app would stop them."""
    import inspect

    from app.admin_pages import gradebook

    source = inspect.getsource(gradebook._deadline_banner)
    assert "st.stop" not in source
    # The only bare `return` guards the not-overdue path, after drawing
    # the informational caption — it must not sit under the warning.
    warning_at = source.index("st.warning")
    assert "return" not in source[warning_at:]
