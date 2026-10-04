"""Whether a term's grade encoding is still open.

One definition, shared by the Gradebook (which stops accepting grades) and
the parent-notice sender (which refuses to email a term card until grades
can no longer change, spec §78.4). Pure: it takes the term's OPEN/CLOSED
switch as a bool so this module never imports `app.models`.
"""

from datetime import date


def days_past_deadline(deadline: date | None, today: date | None = None) -> int | None:
    """How many days late encoding is, or None when it isn't (or when the
    term has no deadline set).

    Kept separate from the drawing so the boundary is testable: on the
    deadline itself nothing is late, and the day after is one day late.
    """
    if deadline is None:
        return None
    overdue = ((today or date.today()) - deadline).days
    return overdue if overdue > 0 else None


def encoding_is_open(status_open: bool, deadline: date | None, today: date) -> bool:
    """Whether teachers may encode a term's grades.

    Two gates, both must pass: the term's OPEN/CLOSED switch, and — when a
    deadline is set — the calendar. Until 2026-09-26 the deadline was
    advisory (a warning banner) and only the switch closed encoding; the
    school decided a set deadline should close it too. The deadline day
    itself is still open (`days_past_deadline`). To let a late teacher
    finish, a Super Admin moves or clears the deadline on School Years &
    Terms — the switch alone no longer reopens a past-deadline term.
    """
    return status_open and days_past_deadline(deadline, today=today) is None
