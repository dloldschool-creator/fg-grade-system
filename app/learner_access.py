"""Which learners a person may *edit*, as opposed to look up (§3C, §54).

§3C gives a Class Adviser "manage learners in assigned section", and §54
spells out the consequence: "Adviser sees learners only in assigned
sections unless additionally authorized." The Learner Masterlist was the
one adviser-facing page that never applied it — every other page reaches
its learners through `section_picker(adviser_user_id=...)`, while this
one searched `learners` directly, so any adviser could retype any of the
school's LRNs.

**Two rules, not one.** An adviser owns

1. every learner enrolled in a section they advise, and
2. every learner they created who is not enrolled anywhere.

The second exists because of a real sequence, not a hypothetical: the
bulk-add panel refuses a Section the uploader does not advise **and still
creates the learners** — deliberately, since blocking them entirely is
worse. Rule 1 alone would hand those rows to nobody the instant the page
reloaded, including the person who had just typed them. A learner who is
enrolled somewhere else has an adviser of their own and leaves this set.

`created_by_user_id` is NULL for every learner that predates the column,
so those resolve to registrar-only. That is the fail-safe direction: an
unowned row is not an everyone's row.

**Sections are not filtered by school year here.** A section carries its
own year and its own adviser, so "a section I advise" already means the
years I advised it in, and last year's advisees stay reachable for the
name corrections that surface when a document is printed. Narrowing to
the current year would be defensible too; it is not worth the extra
parameter to get wrong in one caller and not another.

The comparison is done in SQL on purpose. `AuthUser.id` is a `str` and
`created_by_user_id` is a `uuid.UUID`; Postgres coerces between them,
Python does not. Anything comparing them in Python goes through
`app.section_access.is_advised_by` instead — see its docstring for the
bug that rule came from.
"""


def editable_learner_ids(session, adviser_user_id) -> set:
    """The learner ids `adviser_user_id` may edit, as one set.

    Two queries, both `IN (...)`-shaped, and the caller runs this once per
    render above any loop — the Learner Masterlist draws one expander per
    learner, and Streamlit runs an expander body whether or not it is
    open.

    Pass `None` for a Registrar or Super Admin; they are not scoped by
    this and calling here for them would be a wasted round trip, so it
    returns an empty set rather than pretending to answer.

    Models are imported inside the function so this module stays free of
    load-time `app.models`, the way `section_access` and `section_picker`
    are. It is imported from page code and is small enough to be an easy
    one to 'tidy' upward.
    """
    from app.models.academic_structure import Section
    from app.models.learners import Enrollment, Learner

    if adviser_user_id is None:
        return set()

    advised = [
        row[0]
        for row in session.query(Section.id)
        .filter(Section.adviser_user_id == adviser_user_id)
        .all()
    ]
    enrolled = (
        {
            row[0]
            for row in session.query(Enrollment.learner_id)
            .filter(Enrollment.section_id.in_(advised))
            .all()
        }
        if advised
        else set()
    )

    # Left join rather than NOT EXISTS: one plan, and it reads as the
    # sentence it implements — mine, and enrolled nowhere.
    unenrolled_mine = {
        row[0]
        for row in session.query(Learner.id)
        .outerjoin(Enrollment, Enrollment.learner_id == Learner.id)
        .filter(
            Learner.created_by_user_id == adviser_user_id,
            Enrollment.id.is_(None),
        )
        .all()
    }
    return enrolled | unenrolled_mine


def may_edit(learner_id, editable_ids, adviser_user_id) -> bool:
    """The guard to repeat inside a Save or Delete handler.

    Rendering the right controls is not the same as enforcing the rule:
    Streamlit re-runs the whole script per interaction, and a submitted
    form is handled by the run that drew it — but the set is already in
    hand, so re-asking costs nothing and the check sits next to the
    write it protects rather than a screen away from it.

    `adviser_user_id` of None is an unscoped account, which may edit
    anything.
    """
    return adviser_user_id is None or learner_id in editable_ids
