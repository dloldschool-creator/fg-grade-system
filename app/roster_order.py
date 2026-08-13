"""The order learners are listed in, in one place.

Every roster the school reads — the gradebook, the grade summary, the
attendance grid and SF2 — puts **males first, then females, alphabetical
within each group**. That is how the DepEd forms are laid out (§34) and
how the teachers' own workbook has always been organised, so a screen
that ordered them any other way would make a teacher check names off
against a list that doesn't match the paper in front of them.

Kept here rather than repeated per page because the failure is silent:
a page with its own ORDER BY still renders a perfectly plausible roster,
just not the one next to it.

Two entry points, because half the callers sort in SQL and half already
hold the rows:

    query.order_by(*learner_order_by(Learner))   # the database sorts
    rows.sort(key=learner_sort_key)              # a list already in hand

Both produce the same order. `learner_sort_key` reads the sex as a plain
string so it works on a Learner, a row tuple, or anything else carrying
`.sex`, `.last_name` and `.first_name`.
"""

from sqlalchemy import case

from app.models.enums import Sex

# Anything that isn't MALE or FEMALE sorts last rather than raising. Sex
# is required on a learner, so this only ever catches a partially-built
# row — better a name at the bottom of the list than a crashed page.
_SEX_RANK = {Sex.MALE.value: 0, Sex.FEMALE.value: 1}
_UNKNOWN_SEX_RANK = 2


def learner_order_by(model):
    """ORDER BY clauses for a query that has `model` (usually `Learner`)
    joined or selected.

    A CASE rather than ordering on the column itself: the stored values
    are the strings "MALE" and "FEMALE", and sorting those alphabetically
    puts FEMALE first — which is exactly the bug this module was written
    to fix.
    """
    return (
        case((model.sex == Sex.MALE, 0), (model.sex == Sex.FEMALE, 1), else_=_UNKNOWN_SEX_RANK),
        model.last_name,
        model.first_name,
    )


def learner_sort_key(learner):
    """Sort key for a list of learners already loaded.

    Pass a Learner, or use `key=lambda row: learner_sort_key(row[1])` for
    a tuple whose second element is the learner.
    """
    sex = getattr(learner.sex, "value", learner.sex)
    return (
        _SEX_RANK.get(sex, _UNKNOWN_SEX_RANK),
        learner.last_name or "",
        learner.first_name or "",
    )
