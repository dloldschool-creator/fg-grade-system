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

# Short enough that a teacher who has just submitted sees it reflected
# while a head is watching the page during encoding week, long enough
# that dragging the filters costs nothing.
CACHE_TTL_SECONDS = 120


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Reading learners at risk…")
def _at_risk(school_year_id: str):
    """Cached at-risk list.

    **This one holds learner names**, which makes the cache-key rule
    below concrete rather than theoretical. It is shared across everyone
    signed in, and is safe today only because the three roles that can
    open this page already see every learner in the school. Anything that
    narrows a viewer to a subset — an adviser, a subject teacher — has to
    become an argument to this function on the same day it gains access.
    """
    with get_session() as session:
        return analytics_service.at_risk_learners(session, school_year_id)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Reading grade statistics…")
def _grade_stats(school_year_id: str):
    """Cached distribution and difficulty source, same contract as
    `_encoding_progress` below — including the warning about the cache
    key and who is allowed to read it."""
    with get_session() as session:
        return analytics_service.subject_grade_stats(session, school_year_id)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Reading encoding progress…")
def _encoding_progress(school_year_id: str):
    """Cached aggregate for one school year.

    The session is opened *inside* the cached function — a `Session` is
    not cacheable, and ORM instances handed across a cache boundary come
    back detached. `analytics_service` returns frozen dataclasses of
    primitives for exactly this reason.

    **The argument list is the cache key, and `st.cache_data` is shared
    across every signed-in user.** That is safe only while this page is
    limited to the three roles that already see the whole school. The day
    an adviser or subject teacher reaches it, whatever scopes their view
    has to become an argument here, or one adviser will be served
    another's cached sections. See the Learner Masterlist entry in
    CLAUDE.md for the same failure without a cache in front of it.
    """
    with get_session() as session:
        return analytics_service.encoding_progress(session, school_year_id)


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
    st.dataframe(frame, hide_index=True, use_container_width=True)

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
    st.altair_chart(chart, use_container_width=True)

    frame["Share"] = frame["Share"].map(lambda v: f"{v:.1f}%")
    st.dataframe(frame, hide_index=True, use_container_width=True)
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
    st.dataframe(frame, hide_index=True, use_container_width=True)

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
        st.altair_chart(chart, use_container_width=True)
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
    require_role("SUPER_ADMIN", "REGISTRAR", "SCHOOL_HEAD")
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
    rows = _encoding_progress(str(sy_choice))
    if not rows:
        st.info("This school year has no sections or no terms yet.")
        return
    stats = _grade_stats(str(sy_choice))
    risk = _at_risk(str(sy_choice))

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

    st.dataframe(_table(shown), hide_index=True, use_container_width=True)

    placeholders = sum(row.placeholder_offerings for row in shown)
    if placeholders:
        st.warning(
            f"{placeholders} of the subjects counted above are still "
            "placeholders — a slot like \"Elective 2\" rather than a named "
            "subject. They are counted so this figure matches what teachers "
            "can actually encode against, but each one needs a real subject "
            "chosen on Section Subject Offerings."
        )

    st.divider()
    st.subheader("Grade distribution")
    _render_distribution(stats, shown_grades)

    st.divider()
    st.subheader("Subject difficulty")
    _render_difficulty(stats, shown_grades)

    st.divider()
    st.subheader("Learners at risk")
    _render_at_risk(risk, shown_risk)
