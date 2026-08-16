"""Shared helpers for admin pages."""

import csv
import io
from contextlib import contextmanager

import streamlit as st
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal

_FLASH_KEY = "_flash_messages"
_TEXT_GENERATION = "_text_field_generation_"
ALL = "— all —"

# st.toast takes an emoji, not a message kind, so the mapping lives here
# rather than being guessed at each call site.
_TOAST_ICONS = {"success": "✅", "error": "🚫", "warning": "⚠️", "info": "ℹ️"}


@contextmanager
def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def picker_options(sections, grade_levels, strands, grade_choice=ALL):
    """Which grade levels and strands a section list actually offers.

    **Strand cascades from grade level.** A strand is offered only when a
    section in the chosen grade level uses it — the school's strands do not
    all run in both grade levels, and picking one that does not can only
    ever produce "No sections match those filters." Pass `ALL` and every
    strand present anywhere in `sections` comes back.

    A `grade_choice` that is not in the list is treated as `ALL` rather
    than narrowing to nothing, which is what a selection left over from
    another school year looks like.

    Pure, and separate from the widgets, so the cascade is unit-tested
    without a database or a Streamlit run. `section_picker` is the only
    caller.
    """
    present_grades = [
        g for g in grade_levels if any(s.grade_level_id == g.id for s in sections)
    ]
    if grade_choice not in {g.id for g in present_grades}:
        grade_choice = ALL
    in_grade = [
        s for s in sections if grade_choice == ALL or s.grade_level_id == grade_choice
    ]
    present_strands = [
        s for s in strands if any(sec.strand_id == s.id for sec in in_grade)
    ]
    return present_grades, present_strands


def section_filters(sections, grade_levels: dict, strands: dict, *, key: str, extra_slots: int = 0):
    """The Grade level and Strand dropdowns that sit above a list of sections.

    Shared so the two callers cannot drift apart: `section_picker` asks for
    one trailing slot and puts its Section dropdown there, while the
    Sections page asks for none and filters the expanders it draws below.
    The cascade, the "a filter only appears when it would narrow anything"
    rule and the stale-choice handling are therefore written once.

    Returns `(sections that survive the filters, the trailing containers)`.
    A caller that asked for slots always gets that many, even when no
    filter was drawn — they fall back to the page itself, full width.
    """
    # Read the grade level out of session state before laying anything out:
    # the strand options depend on it, and Streamlit has already stored the
    # new grade by the time this run draws the strand box.
    grade_key, strand_key = f"{key}_grade", f"{key}_strand"
    grade_choice = st.session_state.get(grade_key, ALL)
    present_grades, present_strands = picker_options(
        sections, grade_levels.values(), strands.values(), grade_choice
    )
    _forget_stale(grade_key, [ALL] + [g.id for g in present_grades])
    _forget_stale(strand_key, [ALL] + [s.id for s in present_strands])

    filters = []
    if len(present_grades) > 1:
        filters.append("grade")
    if len(present_strands) > 1:
        filters.append("strand")

    grade_choice = strand_choice = ALL
    if filters:
        columns = st.columns(len(filters) + extra_slots)
        index = 0
        if "grade" in filters:
            grade_choice = columns[index].selectbox(
                "Grade level",
                options=[ALL] + [g.id for g in present_grades],
                format_func=lambda v: ALL if v == ALL else grade_levels[v].name,
                key=grade_key,
            )
            index += 1
        if "strand" in filters:
            strand_choice = columns[index].selectbox(
                "Strand",
                options=[ALL] + [s.id for s in present_strands],
                format_func=lambda v: ALL if v == ALL else strands[v].name,
                key=strand_key,
            )
            index += 1
        slots = list(columns[index:])
    else:
        slots = [st] * extra_slots

    visible = [
        s
        for s in sections
        if (grade_choice == ALL or s.grade_level_id == grade_choice)
        and (strand_choice == ALL or s.strand_id == strand_choice)
    ]
    return visible, slots


def _forget_stale(key: str, allowed) -> None:
    """Drop a stored filter choice the current options no longer contain.

    Streamlit restores a keyed widget from session state on every rerun, so
    a strand chosen under one grade level is still in state after the grade
    changes underneath it. Clearing it *before* the widget is built lets it
    fall back to its first option; touching the key afterwards would raise.
    """
    if key in st.session_state and st.session_state[key] not in allowed:
        del st.session_state[key]


def section_picker(
    session,
    school_year_id,
    *,
    key: str,
    adviser_user_id=None,
    label: str = "Section",
    empty_message: str | None = None,
):
    """Grade level and strand filters, then a section dropdown.

    One implementation shared by every page that picks a section, so the
    filters behave identically everywhere and a page added later gets
    them for free.

    **A filter only appears when it would narrow anything.** With one
    grade level in the list a "Grade level" dropdown is noise, and the
    school has 30 sections across 2 grade levels and 7 strands — the
    filters exist for that, not for a three-section test database. The
    strand filter is judged on what the chosen grade level leaves, so a
    grade level running a single strand drops the dropdown too.

    Returns the chosen `Section`, or None when there is nothing to pick,
    having already shown the reason.

    **The model imports are deliberately inside the function.** This module
    is imported by every page, so importing models here at module load made
    it the first thing to initialise `app.models` — and that package's
    `__init__` imports its own submodules while still initialising itself.
    Python 3.14 (which the deployed host runs) resolves that re-entry
    differently from 3.13, mapping each table twice and failing with
    "Table is already defined for this MetaData instance". Importing at
    call time keeps `_helpers` free of model imports, which is how it was
    before the section filters were added.
    """
    from app.models.academic_structure import GradeLevel, Section, Strand

    query = session.query(Section).filter_by(school_year_id=school_year_id)
    if adviser_user_id is not None:
        query = query.filter_by(adviser_user_id=adviser_user_id)
    sections = query.order_by(Section.name).all()

    if not sections:
        st.warning(
            empty_message
            or (
                "You're not the adviser of any section for this school year yet."
                if adviser_user_id is not None
                else "No sections for this school year yet."
            )
        )
        return None

    grade_levels = {
        g.id: g for g in session.query(GradeLevel).order_by(GradeLevel.display_order).all()
    }
    strands = {s.id: s for s in session.query(Strand).order_by(Strand.name).all()}

    visible, slots = section_filters(sections, grade_levels, strands, key=key, extra_slots=1)
    target = slots[0]

    if not visible:
        target.selectbox(label, options=["— none match —"], disabled=True, key=f"{key}_none")
        st.info("No sections match those filters.")
        return None

    by_id = {s.id: s for s in visible}
    chosen = target.selectbox(
        label,
        options=[s.id for s in visible],
        format_func=lambda v: by_id[v].name,
        key=f"{key}_section",
    )
    return by_id[chosen]


def flash(kind: str, message: str) -> None:
    """Queues a message to survive an immediately-following st.rerun() —
    st.success/st.error called right before st.rerun() never reach the
    browser, since the rerun restarts script execution before the message
    is rendered. Call render_flashes() near the top of a page to show and
    clear whatever's queued."""
    st.session_state.setdefault(_FLASH_KEY, []).append((kind, message))


def render_flashes() -> None:
    """Shows what flash() queued, twice: in place at the top of the page,
    and as a toast floating over whatever part of the page you are actually
    looking at.

    The toast is the copy you see. Every add form on these pages sits
    *below* the list it adds to, so by the time you press Add the top of
    the page is scrolled off — and a result you have to go looking for
    reads as nothing having happened, which is worst for the errors.

    The inline copy stays because a toast dismisses itself after a few
    seconds; "Couldn't save — that subject code is already used" needs to
    still be there while you fix it.
    """
    for kind, message in st.session_state.pop(_FLASH_KEY, []):
        getattr(st, kind)(message)
        st.toast(message, icon=_TOAST_ICONS.get(kind))


def text_field(label: str, *, key: str, area: bool = False, container=None, **kwargs):
    """A text box an add form can blank again once the row is saved.

    `key` is "<form>.<field>"; `clear_text_fields("<form>")` empties every
    box built under that form and touches nothing else.

    The real key handed to Streamlit carries a per-form generation number
    ("add_section.name#3"). That is what makes clearing work *in a
    browser*: see clear_text_fields.

    `container` takes a column (`col1`) for forms laid out side by side,
    since `col1.text_input(...)` cannot go through this function.
    """
    form = key.split(".", 1)[0]
    generation = st.session_state.get(_TEXT_GENERATION + form, 0)
    target = container if container is not None else st
    widget = target.text_area if area else target.text_input
    return widget(label, key=f"{key}#{generation}", **kwargs)


def clear_text_fields(form: str) -> None:
    """Empties one add form's text boxes, leaving its tick boxes and
    pickers as they are.

    Adding rows is repetitive work: the grade level, the track, the term,
    the "Active" tick are usually the same for the next row, while the code
    and the name are exactly what must not be left sitting there to be
    submitted twice. (`clear_on_submit=True` would reset the lot.)

    **Deleting the widget's session_state key is not enough, and looks like
    it is.** The server-side value does clear — an AppTest sees an empty
    box, and so does any test that never opens a browser. But a widget
    inside `st.form` also holds a value in the *frontend*, which the form
    keeps across the rerun and re-submits; the box still reads "STEM - A"
    while the server believes it is empty. That shipped once, on Sections.

    So the clear works by identity instead: bumping the form's generation
    gives every one of its text boxes a key Streamlit has never seen, and a
    brand-new widget has nothing to restore. Nothing is deleted, so no
    other widget can be caught by it — a tick box keeps its own key and its
    own value.
    """
    st.session_state[_TEXT_GENERATION + form] = (
        st.session_state.get(_TEXT_GENERATION + form, 0) + 1
    )


def stateful_tabs(key: str, labels: list[str]) -> str:
    """st.tabs() always resets to the first tab after st.rerun() (a known
    Streamlit limitation, not fixed upstream) — every save/delete action
    on these pages reruns to refresh the list, which was kicking the admin
    back to the first tab after every action. This persists the active
    tab in session_state instead, styled to read like tabs."""
    if key not in st.session_state:
        st.session_state[key] = labels[0]
    return st.radio(
        "Section", labels, key=key, horizontal=True, label_visibility="collapsed"
    )


_CONSTRAINT_MESSAGES = {
    "lrn_format": "LRN must be exactly 12 digits.",
    "uq_learners_lrn": "That LRN is already used by another learner.",
    "uq_enrollments_learner_id": "This learner already has an enrollment for that school year.",
    "uq_subject_profile_subjects_subject_profile_id": "That subject is already on this profile.",
    "uq_section_subject_offerings_section_id": "That subject is already offered in this section/term.",
    # No uq_sections_adviser_per_school_year entry: that index was dropped
    # on 2026-08-16 (an adviser may hold several sections — the SNED ones
    # are one adviser over two). The Sections page warns instead of
    # refusing. See app/models/academic_structure.py.
    "uq_tracks_code": "That track code is already used.",
    "uq_strands_track_id": "That strand code is already used for this track.",
    "uq_schools_deped_school_id": "That DepEd School ID is already used.",
    "uq_school_years_name": "That school year name is already used.",
    "uq_subject_categories_code": "That category code is already used.",
    "uq_subjects_code": "That subject code is already used.",
    "uq_grading_policy_versions_grading_policy_id": "That version number already exists for this policy.",
}


def _friendly_integrity_error(exc: IntegrityError) -> str:
    cause = str(getattr(exc, "orig", exc))
    for constraint_name, message in _CONSTRAINT_MESSAGES.items():
        if constraint_name in cause:
            return message
    # Unrecognized constraint — surface Postgres's own detail line rather
    # than a fully opaque message, so an unmapped constraint is still
    # self-diagnosable instead of a dead end.
    detail_line = next((line for line in cause.splitlines() if line.strip()), cause).strip()
    return f"that would violate a data rule — {detail_line}"


def flush_or_rollback(session) -> bool:
    """Flushes pending inserts so a generated PK is available to a
    dependent row added right after (e.g. a new Learner's id, needed by a
    new Enrollment referencing it) — with the same friendly-error handling
    as try_commit. Needed because this codebase declares no ORM
    relationship() between related tables (explicit queries everywhere
    instead), so SQLAlchemy's unit-of-work can't infer cross-table insert
    order from a bare FK column alone; without an explicit flush here, it
    can attempt the dependent INSERT first and violate the FK. Call this
    between adding the parent row and the row that references it, then
    still call try_commit for the actual commit."""
    try:
        session.flush()
        return True
    except IntegrityError as exc:
        session.rollback()
        flash("error", f"Couldn't save — {_friendly_integrity_error(exc)}")
        return False


def try_commit(session, success_message: str) -> bool:
    """Commits and flashes `success_message`, or on constraint violation
    rolls back and flashes a friendly explanation instead of a stack
    trace. Returns True on success. Always flashes rather than calling
    st.success/st.error directly, since call sites follow this with
    st.rerun()."""
    try:
        session.commit()
        flash("success", success_message)
        return True
    except IntegrityError as exc:
        session.rollback()
        flash("error", f"Couldn't save — {_friendly_integrity_error(exc)}")
        return False


def read_uploaded_csv(uploaded_file) -> list[dict]:
    """Parses a Streamlit-uploaded CSV into a list of {column: value}
    dicts, keyed by the header row, with surrounding whitespace stripped
    from every value. Lightweight bulk-import, not the full formal
    upload/map/validate/confirm/audit pipeline in master-spec.md §51 —
    that stays Phase 13; this is fixed-column CSV with inline validation
    (see each page's own row-validation loop)."""
    text = uploaded_file.getvalue().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [{k: (v or "").strip() for k, v in row.items()} for row in reader]


def try_delete(session, instance, label: str) -> bool:
    """Deletes `instance` and commits. On FK violation (row still
    referenced elsewhere — every FK in this schema is ON DELETE RESTRICT),
    flashes a friendly message instead of a stack trace. Returns True on
    success. Always flashes rather than calling st.success/st.error
    directly, since every call site follows this with st.rerun()."""
    try:
        session.delete(instance)
        session.commit()
        flash("success", f"Deleted {label}.")
        return True
    except IntegrityError:
        session.rollback()
        flash("error", f"Can't delete {label} — it's still referenced elsewhere.")
        return False
