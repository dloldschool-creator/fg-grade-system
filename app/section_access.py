"""Who may act on a section (§3C), in one place.

Deliberately tiny and dependency-free — no models, no Streamlit — so any
module can import it without affecting import order. `app/models`
re-entrancy is what took the live app down on 2026-08-12; a helper this
small should never be the reason a page can't import something.

**The comparison is the whole point.** `AuthUser.id` is our `users.id`
as a `str`; every `*_user_id` column is a `uuid.UUID`. Postgres coerces
between them, so a SQL `filter_by(adviser_user_id=current_user.id)`
matches — and the same two values compared in Python never do. That
mismatch shipped once, in the adviser bulk-enrol check on 2026-08-17,
and told an adviser her own section wasn't hers while the panel above it
listed that section correctly. Every such comparison now goes through
here.
"""


def is_advised_by(section, user_id) -> bool:
    """True when `section` is advised by `user_id`.

    Both sides are coerced to `str`, so it does not matter whether the
    caller holds an `AuthUser.id` (str) or an ORM `uuid.UUID`. A section
    with no adviser is nobody's: `None` never equals a real id.
    """
    if section is None or section.adviser_user_id is None or user_id is None:
        return False
    return str(section.adviser_user_id) == str(user_id)


def may_reopen_attendance_month(user, section) -> bool:
    """§33: a Super Admin or Registrar may reopen any section's finalized
    month; an adviser only their own section's. A reason is still required
    and the reopen is still audited — that is the page's job, not this one's.

    The adviser half goes through `is_advised_by`, never `==`, for the
    str-vs-UUID reason at the top of this module.
    """
    if user is None:
        return False
    if user.has_role("SUPER_ADMIN", "REGISTRAR"):
        return True
    return user.has_role("ADVISER") and is_advised_by(section, user.id)


def may_acknowledge_reprint(user, section) -> bool:
    """Who may confirm a reopened month's SF2/SF4 was printed again (§33):
    the same people who may reopen it — an admin, a registrar, or the
    section's own adviser. A School Head only views, so is excluded the same
    way. Kept as its own name so the two rules can diverge deliberately."""
    return may_reopen_attendance_month(user, section)
