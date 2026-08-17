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
