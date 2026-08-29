"""Overview → Insights: filtered analytics over already-computed figures.

Sits beside the Dashboard rather than inside it, and the split is by
question. The Dashboard answers *what is outstanding right now* and is
deliberately filter-free; this page answers *how are we doing* and is
nothing but filters. Streamlit re-runs the whole script on every widget
interaction, so merging them would make a strand change re-pay for the
attendance-month table nobody moved.

Read-only by construction, like the Dashboard: no control here writes
anything, which is a stronger guarantee for a School Head than hiding
buttons.

**Every filter is free.** The aggregate is fetched once per school year,
cached, and sliced in Python. One section × term row per combination is
90 rows today, so the whole year costs less memory than a single roster
and no dropdown costs a round trip. The expensive half — 32,000 grade
rows — is counted in Postgres and never crosses the wire; see
`app/analytics_service.py`.
"""

import altair as alt
import pandas as pd
import streamlit as st

from app import analytics_service
from app.admin_pages._helpers import (
    ALL,
    _forget_stale,
    get_session,
    render_flashes,
    section_filters,
)
from app.auth import require_role
from app.models.organization import SchoolYear

DASH = "—"

# Roles that see the whole school. Anyone else reaching this page is
# scoped to the sections they advise — see `render`.
SCHOOL_WIDE_ROLES = frozenset({"SUPER_ADMIN", "REGISTRAR", "SCHOOL_HEAD"})

# Short enough that a teacher who has just submitted sees it reflected
# while a head is watching the page during encoding week, long enough
# that dragging the filters costs nothing.
CACHE_TTL_SECONDS = 120


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Reading learners at risk…")
def _at_risk(school_year_id: str, section_ids):
    """Cached at-risk list.

    **This one holds learner names**, which is why `section_ids` is in
    the key and not merely in the query. See `_encoding_progress`.
    """
    with get_session() as session:
        return analytics_service.at_risk_learners(session, school_year_id, section_ids)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Reading grade statistics…")
def _grade_stats(school_year_id: str, section_ids, offering_ids=None):
    """Cached distribution and difficulty source, same contract as
    `_encoding_progress` below."""
    with get_session() as session:
        return analytics_service.subject_grade_stats(
            session, school_year_id, section_ids, offering_ids
        )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Reading encoding progress…")
def _encoding_progress(school_year_id: str, section_ids):
    """Cached aggregate for one school year.

    The session is opened *inside* the cached function — a `Session` is
    not cacheable, and ORM instances handed across a cache boundary come
    back detached. `analytics_service` returns frozen dataclasses of
    primitives for exactly this reason.

    **`section_ids` is in the signature because the argument list is the
    cache key, and `st.cache_data` is shared across every signed-in
    user.** `None` is the whole school, for the roles that may see it; an
    adviser passes the tuple of sections they hold, so two advisers can
    never collide on one entry. Scoping the query without scoping the key
    would be worse than not scoping at all — it would look right and
    serve whichever adviser asked first. See the Learner Masterlist entry
    in CLAUDE.md for the same failure without a cache in front of it.
    """
    with get_session() as session:
        return analytics_service.encoding_progress(session, school_year_id, section_ids)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Reading subject progress…")
def _offering_progress(school_year_id: str, section_ids=None, offering_ids=None):
    """Cached per-subject view. Same key contract as above — both scopes
    are arguments, so a teacher and an adviser can never share an entry."""
    with get_session() as session:
        return analytics_service.offering_progress(
            session, school_year_id, section_ids, offering_ids
        )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _taught_offerings(school_year_id: str, user_id: str):
    """Which classes this subject teacher holds, cached per user.

    The teacher counterpart of `_advised_sections`, and the reason every
    teacher-facing cache key below is safe.
    """
    with get_session() as session:
        return analytics_service.taught_offering_ids(session, school_year_id, user_id)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _advised_sections(school_year_id: str, user_id: str):
    """Which sections this adviser holds, cached per user.

    Keyed by `user_id`, which is what makes every other cache key on this
    page safe: they are keyed by the tuple this returns.
    """
    with get_session() as session:
        return analytics_service.advised_section_ids(session, school_year_id, user_id)


class _Dim:
    """The shape `section_filters` reads off a grade level or strand.

    Reusing that helper rather than writing a third filter cascade is the
    point: it already handles the two things that are easy to get wrong —
    a filter only appearing when it would narrow something, and a strand
    choice left in session state after the grade level changed underneath
    it. The cached rows carry their own dimensions, so these stand in for
    the ORM objects without a query.
    """

    __slots__ = ("id", "name")

    def __init__(self, id, name):
        self.id = id
        self.name = name


class _Sec:
    __slots__ = ("id", "name", "grade_level_id", "strand_id")

    def __init__(self, id, name, grade_level_id, strand_id):
        self.id = id
        self.name = name
        self.grade_level_id = grade_level_id
        self.strand_id = strand_id


def _dimensions(rows):
    """Unique grade levels, strands and sections present in `rows`.

    Ordered by the same keys the service sorted the rows by, so the
    dropdowns read in the school's order rather than insertion order.
    """
    grade_levels, strands, sections = {}, {}, {}
    for row in rows:
        if row.grade_level_id is not None:
            grade_levels.setdefault(row.grade_level_id, _Dim(row.grade_level_id, row.grade_level_name))
        if row.strand_id is not None:
            strands.setdefault(row.strand_id, _Dim(row.strand_id, row.strand_name))
        sections.setdefault(
            row.section_id,
            _Sec(row.section_id, row.section_name, row.grade_level_id, row.strand_id),
        )
    return grade_levels, strands, list(sections.values())


def _term_options(rows):
    terms = {}
    for row in rows:
        terms.setdefault(row.term_id, (row.term_number, row.term_name))
    return [tid for tid, _ in sorted(terms.items(), key=lambda kv: kv[1][0])], terms


def _fmt_percent(value: float | None) -> str:
    return DASH if value is None else f"{value:.0f}%"


def _fmt_grade(value: float | None) -> str:
    """Grades are whole numbers everywhere in this system, and a missing
    one is a dash — never a 0, which would read as a score."""
    return DASH if value is None else f"{value:g}"


def _table(rows) -> pd.DataFrame:
    """Furthest behind first.

    `None` percent — a section with nothing offered yet — sorts to the
    bottom rather than the top: it is not 0% done, it is not yet askable,
    and putting it above sections where teachers are genuinely late is
    how a progress list stops being read.
    """
    ordered = sorted(
        rows,
        key=lambda r: (r.percent is None, r.percent if r.percent is not None else 0, -r.missing),
    )
    return pd.DataFrame(
        [
            {
                "Grade": row.grade_level_name or DASH,
                "Strand": row.strand_name or DASH,
                "Section": row.section_name,
                "Term": row.term_name,
                "Learners": row.active_learners,
                "Subjects": row.offerings,
                "Expected": row.expected,
                "Encoded": row.encoded,
                "Missing": row.missing,
                "Progress": _fmt_percent(row.percent),
            }
            for row in ordered
        ]
    )


def _chart_by_section(rows) -> pd.DataFrame | None:
    """Percent encoded per section, rolled up across whatever terms are
    in view.

    Rolled up through `analytics_service.roll_up`, never by averaging the
    per-term percentages — a section with one subject in T1 and nine in
    T2 is not half-done because one of its two terms is.
    """
    by_section: dict = {}
    for row in rows:
        by_section.setdefault((row.section_name, row.grade_level_name), []).append(row)

    data = []
    for (section_name, grade_name), group in by_section.items():
        _encoded, expected, percent = analytics_service.roll_up(group)
        if expected == 0:
            continue
        data.append({"Section": f"{section_name} ({grade_name})", "% encoded": percent})
    if not data:
        return None
    frame = pd.DataFrame(data).sort_values("% encoded")
    return frame.set_index("Section")


def _render_at_risk(report, rows) -> None:
    """The learners a term summary says are failing.

    The only part of this page that names people, so it shows the least
    that still makes someone findable: name, section, term. No birthdate
    and no LRN — an LRN is not needed to identify a learner to their own
    school, and it is the one identifier this system is careful never to
    put anywhere it could travel.
    """
    if not rows:
        st.success(
            "No learner is currently below the passing mark in a computed "
            "term summary for this selection."
        )
        return

    learners, flags = analytics_service.at_risk_headline(rows)
    provisional = sum(1 for row in rows if row.provisional)

    col1, col2 = st.columns(2)
    col1.metric("Learners at risk", f"{learners:,}")
    col2.metric("Term flags", f"{flags:,}")
    if learners != flags:
        st.caption(
            "A learner flagged in more than one term counts once as a "
            "learner and once per term as a flag."
        )

    frame = pd.DataFrame(
        [
            {
                "Learner": row.learner_name,
                "Grade": row.grade_level_name or DASH,
                "Section": row.section_name,
                "Term": row.term_name,
                "Subjects failed": row.failed_subjects,
                "Lowest grade": _fmt_grade(row.lowest_grade),
                "Term average": _fmt_grade(row.term_average),
                "Record": "Still encoding" if row.provisional else "Complete",
            }
            for row in rows
        ]
    )
    st.dataframe(frame, hide_index=True, width="stretch")

    if provisional:
        st.warning(
            f"{provisional} of these terms are still being encoded, so the "
            "term average shown is built from part of the subject list and "
            "will move. The failed-subject count is real either way — those "
            "grades are already below the passing mark."
        )
    st.caption(
        "Read from the term summaries the grading pages computed, never "
        "recalculated here, so this agrees with what the report cards say. "
        "A learner nobody has graded yet does not appear — a blank grade is "
        "not a low one."
    )


def _render_by_subject(rows) -> None:
    """Per-subject progress for a single section, least done first.

    This is the class adviser's question. Section-and-term progress says
    whether they are behind; this says on what, and whose door to knock
    on — the adviser does not encode these grades, the subject teacher
    does, but the adviser is the one holding the report card.
    """
    if not rows:
        st.caption("This section has no subjects offered yet.")
        return

    frame = pd.DataFrame(
        [
            {
                "Term": row.term_name,
                "Subject": row.subject_name or row.subject_code,
                "Teacher": row.teacher_name or "Not assigned",
                "Encoded": f"{row.encoded} / {row.expected}",
                "Missing": row.missing,
                "Progress": _fmt_percent(row.percent),
            }
            for row in rows
        ]
    )
    st.dataframe(frame, hide_index=True, width="stretch")

    unassigned = [r for r in rows if not r.teacher_name]
    if unassigned:
        st.warning(
            f"{len(unassigned)} of these subjects have no teacher assigned, "
            "so nobody has been asked to encode them. They can be assigned "
            "on Teacher Assignments."
        )


def _render_my_classes(rows) -> None:
    """The subject teacher's own view: one row per class they hold.

    Covers §42's list for them — assigned subjects, assigned sections,
    term, grades entered, submission status and deadline — from a single
    scoped query.

    **It does not name the learners still missing a grade.** The count is
    here, and the Gradebook is where you act on it: that page already
    shows the whole class with the blanks visible, and reprinting the
    names here would be a second roster to keep in step with it.
    """
    if not rows:
        st.info(
            "You have no active teaching assignments in this school year. "
            "A Super Admin or your adviser assigns classes on the Teacher "
            "Assignments page."
        )
        return

    encoded = sum(r.encoded for r in rows)
    expected = sum(r.expected for r in rows)
    submitted = sum(r.submitted for r in rows)
    col1, col2, col3 = st.columns(3)
    col1.metric("Classes", len({(r.section_id, r.subject_id, r.term_id) for r in rows}))
    col2.metric(
        "Grades encoded",
        _fmt_percent(100.0 * encoded / expected if expected else None),
        help=f"{encoded:,} of {expected:,} expected",
    )
    col3.metric(
        "Submitted",
        _fmt_percent(100.0 * submitted / encoded if encoded else None),
        help=f"{submitted:,} of the {encoded:,} you have encoded",
    )

    frame = pd.DataFrame(
        [
            {
                "Section": row.section_name,
                "Subject": row.subject_name or row.subject_code,
                "Term": row.term_name,
                "Encoded": f"{row.encoded} / {row.expected}",
                "Still to encode": row.missing,
                "Submitted": row.submitted,
                "Encoding": row.term_encoding_status or DASH,
                "Deadline": (
                    f"{row.submission_deadline:%d %b %Y}"
                    if row.submission_deadline
                    else DASH
                ),
            }
            for row in rows
        ]
    )
    st.dataframe(frame, hide_index=True, width="stretch")

    # Encoding and submitting are separate steps (rule 7), so a class can
    # be fully typed up and still not handed in. That is the state most
    # worth saying out loud near a deadline.
    unsubmitted = [r for r in rows if r.encoded and r.submitted < r.encoded]
    if unsubmitted:
        st.warning(
            f"{len(unsubmitted)} of your classes have grades encoded but not "
            "yet submitted. Encoding saves your work; submitting is what "
            "hands it in. Both are on the Gradebook."
        )
    st.caption(
        "Grades still to encode are counted against the learners currently "
        "on each class roll. A blank is not a zero — it means nobody has "
        "been graded there yet."
    )


def _render_teacher_view(school_year_id: str, current_user) -> None:
    """The whole page, for a subject teacher.

    A separate branch rather than the school-wide layout with a filter
    on it, because a subject teacher's entitlement is a different shape:
    they own classes, not sections. The section-level encoding table,
    the school-wide difficulty ranking and the at-risk list — which
    reads whole-term averages across every subject — all describe
    things outside what they teach, so none of them appears here.
    """
    offering_ids = _taught_offerings(school_year_id, str(current_user.id))
    rows = _offering_progress(school_year_id, None, offering_ids)

    st.subheader("My classes")
    _render_my_classes(rows)
    if not rows:
        return

    stats = _grade_stats(school_year_id, None, offering_ids)
    st.divider()
    st.subheader("Grade distribution")
    st.caption("Across the classes you teach.")
    _render_distribution(stats, list(stats.rows))


def _matches(row, visible_ids, term_choice, section_choice) -> bool:
    """The one filter predicate, applied to both datasets.

    They carry the same section and term ids at different grains, and
    filtering them separately is how the distribution comes to describe a
    different set of sections than the progress table above it.
    """
    return (
        row.section_id in visible_ids
        and (term_choice == ALL or row.term_id == term_choice)
        and (section_choice == ALL or row.section_id == section_choice)
    )


def _render_distribution(stats, rows) -> None:
    """How the encoded grades are spread across the bands."""
    counts = analytics_service.distribution(rows, stats.bands)
    total = sum(count for _band, count in counts)
    if not total:
        st.info("No grades have been encoded for this selection yet.")
        return

    below = counts[0][1]
    col1, col2, col3 = st.columns(3)
    col1.metric("Grades encoded", f"{total:,}")
    col2.metric(f"Below {stats.passing_grade:g}", f"{below:,}")
    col3.metric("Share below passing", _fmt_percent(100.0 * below / total))

    frame = pd.DataFrame(
        [
            {
                "Band": band.label,
                "Grades": count,
                "Share": 100.0 * count / total,
            }
            for band, count in counts
        ]
    )
    # Altair rather than st.bar_chart because the band order is the whole
    # point of the chart and must not be sorted alphabetically — which
    # would put "Below 75" after "90 and above". Altair ships with
    # Streamlit, so this adds nothing to deploy.
    chart = (
        alt.Chart(frame)
        .mark_bar()
        .encode(
            x=alt.X("Band:N", sort=None, title=None),
            y=alt.Y("Grades:Q", title="Grades encoded"),
            tooltip=["Band", "Grades"],
        )
    )
    st.altair_chart(chart, width="stretch")

    frame["Share"] = frame["Share"].map(lambda v: f"{v:.1f}%")
    st.dataframe(frame, hide_index=True, width="stretch")
    st.caption(
        "Counts are encoded term grades, one per learner per subject per "
        "term. This page does not average across subjects — a learner's "
        "own averages are on their report card, where each subject is "
        "weighted by its units."
    )


def _render_difficulty(stats, rows) -> None:
    """Which subjects sit lowest, ranked by the share failing."""
    ranked = analytics_service.subject_difficulty(rows)
    if not ranked:
        st.info("No grades have been encoded for this selection yet.")
        return

    frame = pd.DataFrame(
        [
            {
                "Subject": entry.subject_name or entry.subject_code,
                "Graded": entry.graded,
                "Average": f"{entry.average:.1f}" if entry.average is not None else DASH,
                f"Below {stats.passing_grade:g}": entry.below_passing,
                "Share below": _fmt_percent(entry.percent_below_passing),
                "Lowest": f"{entry.lowest:g}" if entry.lowest is not None else DASH,
                "Highest": f"{entry.highest:g}" if entry.highest is not None else DASH,
            }
            for entry in ranked
        ]
    )
    st.dataframe(frame, hide_index=True, width="stretch")

    # The table above lists every subject; this only trims the chart, so
    # a small subject is never hidden from the reader, just kept out of a
    # ranking it would distort. No `key=`: the maximum moves as encoding
    # goes on, and a stored value above a shrunken maximum is an error
    # rather than a clamp — an auto-keyed slider resets instead.
    biggest = max(entry.graded for entry in ranked)
    minimum = 1
    if biggest > 5:
        minimum = st.slider(
            "Chart only — leave out subjects with fewer grades than this",
            min_value=1,
            max_value=min(biggest, 50),
            value=min(10, biggest),
            help=(
                "Early in a term, a subject with three grades encoded shows "
                "33% below passing on the strength of one learner. Raise "
                "this to keep the chart to subjects with enough grades to "
                "mean something."
            ),
        )

    plotted = [
        e
        for e in ranked
        if e.graded >= minimum and (e.percent_below_passing or 0.0) > 0.0
    ]
    if plotted:
        chart_frame = pd.DataFrame(
            [
                {
                    "Subject": e.subject_name or e.subject_code,
                    "Share below passing": e.percent_below_passing,
                    "Graded": e.graded,
                }
                for e in plotted
            ]
        )
        chart = (
            alt.Chart(chart_frame)
            .mark_bar()
            .encode(
                y=alt.Y("Subject:N", sort="-x", title=None),
                x=alt.X("Share below passing:Q", title="% of grades below passing"),
                tooltip=["Subject", "Share below passing", "Graded"],
            )
        )
        st.altair_chart(chart, width="stretch")
    elif minimum > 1:
        st.caption(
            "No subject with that many grades encoded has anyone below the "
            "passing mark."
        )

    st.caption(
        "Ranked by the share of encoded grades below the passing mark, not "
        "by the average — a subject can sit at a comfortable average and "
        "still have a group of learners failing. Each average covers one "
        "subject only, where every learner carries the same weight for it."
    )


def render() -> None:
    current_user = require_role(
        "SUPER_ADMIN", "REGISTRAR", "SCHOOL_HEAD", "ADVISER", "SUBJECT_TEACHER"
    )
    st.title("Insights")
    st.caption("Nothing on this page changes any data.")
    render_flashes()

    with get_session() as session:
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
    if not school_years:
        st.warning("No school years yet.")
        return

    sy_by_id = {sy.id: sy for sy in school_years}
    sy_choice = st.selectbox(
        "School year",
        options=[sy.id for sy in school_years],
        format_func=lambda v: sy_by_id[v].name,
        key="insights_school_year",
    )

    # str() rather than the UUID so the cache key is a plain value and the
    # same year always hashes the same way.
    # What this viewer is entitled to see, resolved once and then carried
    # into every cache key below. Read off `role_codes` rather than
    # `has_role`, which treats SUPER_ADMIN as satisfying any check — true
    # and harmless for page access, but this is a data question and it
    # should be answered by the roles the account actually holds.
    school_wide = bool(current_user.role_codes & SCHOOL_WIDE_ROLES)
    advises = "ADVISER" in current_user.role_codes

    # **A subject teacher gets a different page, not a narrower one.**
    # Their scope is the classes they teach, which sit inside sections
    # whose other subjects are not theirs to see — so the school-wide
    # layout below, built on whole sections, cannot simply be filtered
    # for them. A teacher who also advises is shown the adviser view,
    # which is the broader of the two entitlements.
    if not school_wide and not advises:
        _render_teacher_view(str(sy_choice), current_user)
        return

    scope_ids = None
    if not school_wide:
        scope_ids = _advised_sections(str(sy_choice), str(current_user.id))
        if not scope_ids:
            st.info(
                "You are not advising a section in this school year, so "
                "there is nothing to show here yet."
            )
            return

    rows = _encoding_progress(str(sy_choice), scope_ids)
    if not rows:
        st.info("This school year has no sections or no terms yet.")
        return
    stats = _grade_stats(str(sy_choice), scope_ids)
    risk = _at_risk(str(sy_choice), scope_ids)

    if scope_ids is not None:
        st.caption(
            f"Showing the {len(scope_ids)} section(s) you advise."
            if len(scope_ids) > 1
            else "Showing the section you advise."
        )

    # One set of filters for the whole page. The dimensions come from the
    # progress rows rather than the grade statistics because those cover
    # every section and term whether or not anything has been encoded —
    # deriving them from the grades would make sections disappear from
    # the dropdowns until somebody graded them.
    grade_levels, strands, sections = _dimensions(rows)
    term_ids, term_labels = _term_options(rows)
    visible_sections, slots = section_filters(
        sections, grade_levels, strands, key="insights", extra_slots=2
    )
    visible_ids = {s.id for s in visible_sections}
    section_ids = [s.id for s in visible_sections]
    name_by_section = {s.id: s.name for s in visible_sections}

    # Both of these carry a choice across a change that can remove it —
    # a term chosen in one school year, a section chosen before the
    # strand filter narrowed past it — and Streamlit raises when a keyed
    # widget's stored value is not among its options. `section_filters`
    # already does this for the two boxes it draws; these two are ours.
    _forget_stale("insights_term", [ALL] + term_ids)
    term_choice = slots[0].selectbox(
        "Term",
        options=[ALL] + term_ids,
        format_func=lambda v: ALL if v == ALL else term_labels[v][1],
        key="insights_term",
    )
    _forget_stale("insights_section", [ALL] + section_ids)
    section_choice = slots[1].selectbox(
        "Section",
        options=[ALL] + section_ids,
        format_func=lambda v: ALL if v == ALL else name_by_section[v],
        key="insights_section",
    )

    shown = [r for r in rows if _matches(r, visible_ids, term_choice, section_choice)]
    if not shown:
        st.info("No sections match those filters.")
        return
    shown_grades = [
        r for r in stats.rows if _matches(r, visible_ids, term_choice, section_choice)
    ]
    shown_risk = [
        r for r in risk.rows if _matches(r, visible_ids, term_choice, section_choice)
    ]

    st.subheader("Grade encoding progress")

    encoded, expected, percent = analytics_service.roll_up(shown)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Progress", _fmt_percent(percent))
    col2.metric("Grades encoded", f"{encoded:,}")
    col3.metric("Expected", f"{expected:,}")
    col4.metric("Still missing", f"{expected - encoded:,}")

    st.caption(
        "**Expected** is active learners × subjects the section offers that "
        "term. **Encoded** counts only grades that carry a number — a blank "
        "grade is not yet encoded, never a zero."
    )

    if term_choice != ALL:
        term_rows = [r for r in shown if r.term_id == term_choice]
        status = term_rows[0].encoding_status
        deadline = term_rows[0].submission_deadline
        parts = []
        if status:
            parts.append(f"Encoding is **{status}** for this term.")
        if deadline:
            parts.append(f"Submission deadline **{deadline:%d %B %Y}**.")
        if parts:
            st.caption(" ".join(parts))

    chart = _chart_by_section(shown)
    if chart is not None and len(chart) > 1:
        st.bar_chart(chart, y="% encoded", horizontal=True)

    st.dataframe(_table(shown), hide_index=True, width="stretch")

    placeholders = sum(row.placeholder_offerings for row in shown)
    if placeholders:
        st.warning(
            f"{placeholders} of the subjects counted above are still "
            "placeholders — a slot like \"Elective 2\" rather than a named "
            "subject. They are counted so this figure matches what teachers "
            "can actually encode against, but each one needs a real subject "
            "chosen on Section Subject Offerings."
        )

    # The per-subject breakdown, once the view is down to one section.
    # Gated on that rather than on the viewer's role: it is the same
    # question whoever asks it, and an adviser with a single section
    # simply arrives here without touching a filter. Running it
    # school-wide would be ~810 rows of detail nobody asked for, and it
    # is a separate query, so the gate keeps it off the school-wide path
    # entirely.
    sections_in_view = {row.section_id for row in shown}
    if len(sections_in_view) == 1:
        only = next(iter(sections_in_view))
        name = next(r.section_name for r in shown if r.section_id == only)
        st.divider()
        st.subheader(f"{name} — by subject")
        by_subject = _offering_progress(str(sy_choice), (only,))
        if term_choice != ALL:
            by_subject = [r for r in by_subject if r.term_id == term_choice]
        _render_by_subject(by_subject)

    st.divider()
    st.subheader("Grade distribution")
    _render_distribution(stats, shown_grades)

    st.divider()
    st.subheader("Subject difficulty")
    _render_difficulty(stats, shown_grades)

    st.divider()
    st.subheader("Learners at risk")
    _render_at_risk(risk, shown_risk)
