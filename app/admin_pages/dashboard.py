"""School-wide dashboard (§3E "view dashboards", "review section summaries").

Read-only by construction: it draws no control that writes anything, so a
School Head reaching it cannot change official data whatever else changes
around it. That is a stronger guarantee than hiding buttons.

Every school figure is read from the already-computed tables — this page
never recomputes a grade or an attendance total, so what a principal sees
here is exactly what the gradebooks and report cards say. The one
exception is the Super Admin's database-space card, which reads Postgres's
own size catalogs and says nothing about grades.
"""

import logging
import os

import pandas as pd
import streamlit as st
from sqlalchemy import func, text

from app.admin_pages._helpers import get_session, render_flashes
from app.analytics_service import ACTIVE_ENROLLMENT_STATUSES
from app.attendance_service import display_state, reprint_cause, reprint_pending
from app.auth import require_role
from app.display_time import format_time
from app.models.academic_structure import GradeLevel, Section, Strand, Track
from app.models.attendance import AttendanceMonthStatus
from app.models.enums import CompletionStatus, FinalizationState, Sex
from app.models.grades import AnnualGradeSummary, TermGrade
from app.models.learners import Enrollment, Learner
from app.models.organization import SchoolYear, Term
from app.models.rbac import User

# The statuses that mean a learner is still on the roll — same set the
# gradebook and SF2 use, so headcounts agree across the app. Kept as an
# alias rather than a fourth copy: Insights reports this page's headcount
# as a percentage, so the two disagreeing is not a cosmetic difference.
ACTIVE_STATUSES = ACTIVE_ENROLLMENT_STATUSES

DASH = "—"

logger = logging.getLogger(__name__)


def _enrollment_overview(session, sy_id) -> list[dict]:
    """One row per section, carrying its grade level, track and strand.

    Sorted by grade level, then track, then strand, then section name, so
    the rows arrive already grouped the way the page displays them and
    the way SF4 reports them.
    """
    sections = session.query(Section).filter_by(school_year_id=sy_id).all()
    if not sections:
        return []

    grade_levels = {g.id: g for g in session.query(GradeLevel).all()}
    tracks = {t.id: t for t in session.query(Track).all()}
    strands = {s.id: s for s in session.query(Strand).all()}
    enrollments = session.query(Enrollment).filter_by(school_year_id=sy_id).all()

    by_section: dict = {}
    for enrollment in enrollments:
        by_section.setdefault(enrollment.section_id, []).append(enrollment)

    summaries = {
        row.enrollment_id: row
        for row in session.query(AnnualGradeSummary)
        .filter(AnnualGradeSummary.enrollment_id.in_([e.id for e in enrollments]))
        .all()
    } if enrollments else {}

    rows = []
    for section in sections:
        members = by_section.get(section.id, [])
        active = [e for e in members if e.enrollment_status in ACTIVE_STATUSES]
        complete = sum(
            1 for e in active
            if e.id in summaries and summaries[e.id].completion_status == CompletionStatus.COMPLETE
        )
        grade_level = grade_levels.get(section.grade_level_id)
        track = tracks.get(section.track_id)
        strand = strands.get(section.strand_id)
        rows.append(
            {
                # Kept for grouping and ordering, dropped before display.
                "_grade_order": grade_level.display_order if grade_level else 0,
                "_grade": grade_level.name if grade_level else DASH,
                "Track": track.name if track else DASH,
                "Strand": strand.name if strand else DASH,
                "Section": section.name,
                "Active": len(active),
                "On roll": len(members),
                "Records complete": f"{complete} / {len(active)}" if active else DASH,
            }
        )

    rows.sort(key=lambda r: (r["_grade_order"], r["Track"], r["Strand"], r["Section"]))
    return rows


def _gender_breakdown(session, sy_id) -> list[dict]:
    """Male / Female / Total per grade level and strand, for active
    enrollees only — one join query, aggregated in Python since the
    roster is a few hundred rows at most.
    """
    rows = (
        session.query(GradeLevel.name, GradeLevel.display_order, Strand.name, Learner.sex)
        .select_from(Enrollment)
        .join(Section, Section.id == Enrollment.section_id)
        .join(GradeLevel, GradeLevel.id == Section.grade_level_id)
        .outerjoin(Strand, Strand.id == Section.strand_id)
        .join(Learner, Learner.id == Enrollment.learner_id)
        .filter(
            Enrollment.school_year_id == sy_id,
            Enrollment.enrollment_status.in_(ACTIVE_STATUSES),
        )
        .all()
    )

    grade_order: dict[str, int] = {}
    buckets: dict[tuple[str, str], dict[str, int]] = {}
    for grade_name, order, strand_name, sex in rows:
        grade_order[grade_name] = order
        key = (grade_name, strand_name or DASH)
        bucket = buckets.setdefault(key, {"Male": 0, "Female": 0})
        if sex == Sex.MALE:
            bucket["Male"] += 1
        elif sex == Sex.FEMALE:
            bucket["Female"] += 1

    result = [
        {
            "_grade_order": grade_order[grade_name],
            "Grade": grade_name,
            "Strand": strand_name,
            "Male": bucket["Male"],
            "Female": bucket["Female"],
            "Total": bucket["Male"] + bucket["Female"],
        }
        for (grade_name, strand_name), bucket in buckets.items()
    ]
    result.sort(key=lambda r: (r["_grade_order"], r["Strand"]))
    return result


def _render_gender_breakdown(rows: list[dict]) -> None:
    """A Strand / Male / Female / Total table per grade level."""
    for grade in dict.fromkeys(row["Grade"] for row in rows):
        block = [row for row in rows if row["Grade"] == grade]
        st.markdown(f"**{grade}**")
        st.dataframe(
            pd.DataFrame(
                [
                    {k: row[k] for k in ("Strand", "Male", "Female", "Total")}
                    for row in block
                ]
                + [
                    {
                        "Strand": "Total",
                        "Male": sum(r["Male"] for r in block),
                        "Female": sum(r["Female"] for r in block),
                        "Total": sum(r["Total"] for r in block),
                    }
                ]
            ),
            hide_index=True,
            width="stretch",
        )


DISPLAY_COLUMNS = ["Track", "Strand", "Section", "Active", "On roll", "Records complete"]


def _render_sections(rows: list[dict]) -> None:
    """A table per grade level, ordered by track then strand within each.

    Grouped rather than one flat list because that is how the school
    thinks about it and how SF4 reports it — and with thirty sections a
    single table is a wall of names.
    """
    for grade in dict.fromkeys(row["_grade"] for row in rows):
        block = [row for row in rows if row["_grade"] == grade]
        active = sum(row["Active"] for row in block)
        strands = len({row["Strand"] for row in block})

        st.markdown(
            f"**{grade}** — {len(block)} section(s), {strands} strand(s), "
            f"{active:,} active learner(s)"
        )
        st.dataframe(
            pd.DataFrame([{k: row[k] for k in DISPLAY_COLUMNS} for row in block]),
            hide_index=True,
            width="stretch",
        )


def _encoding_progress(session, sy_id) -> list[dict]:
    """How far grade encoding has got, per term — the question a school
    head actually asks near a deadline."""
    terms = session.query(Term).filter_by(school_year_id=sy_id).order_by(Term.term_number).all()
    if not terms:
        return []

    counts = dict(
        session.query(TermGrade.term_id, func.count(TermGrade.id))
        .filter(TermGrade.term_id.in_([t.id for t in terms]))
        .group_by(TermGrade.term_id)
        .all()
    )
    submitted = dict(
        session.query(TermGrade.term_id, func.count(TermGrade.id))
        .filter(
            TermGrade.term_id.in_([t.id for t in terms]),
            TermGrade.official_grade.isnot(None),
        )
        .group_by(TermGrade.term_id)
        .all()
    )

    return [
        {
            "Term": term.name,
            "Encoding": term.grade_encoding_status.value if term.grade_encoding_status else DASH,
            "Grade rows": counts.get(term.id, 0),
            "With a grade": submitted.get(term.id, 0),
        }
        for term in terms
    ]


# Shown as the column header and read back to count months awaiting a
# reprint, so one name.
REPRINT_COLUMN = "Reprint SF2/SF4?"

MONTH_COLUMN = st.column_config.DateColumn("Month", format="MMMM YYYY")


def _attendance_status(session, sy_id) -> list[dict]:
    """Which section-months are still open (§33). Blank means nobody has
    started that month, which is different from started-and-incomplete."""
    sections = {
        s.id: s for s in session.query(Section).filter_by(school_year_id=sy_id).all()
    }
    if not sections:
        return []
    statuses = (
        session.query(AttendanceMonthStatus)
        .filter(AttendanceMonthStatus.section_id.in_(list(sections)))
        .all()
    )
    if not statuses:
        return []

    # One query for the names of whoever reopened, or changed after
    # finalizing, a month still awaiting a reprint — the only rows that
    # show a name — not one per row.
    causes = {s.id: reprint_cause(s) for s in statuses if reprint_pending(s)}
    causer_ids = {c.user_id for c in causes.values() if c.user_id}
    causers = (
        {u.id: u.full_name for u in session.query(User).filter(User.id.in_(causer_ids))}
        if causer_ids
        else {}
    )

    # Latest month first, sections alphabetical within a month. year_month
    # is a date pinned to the 1st, so its ordinal orders months.
    rows = []
    for status in sorted(
        statuses,
        key=lambda s: (-s.year_month.toordinal(), sections[s.section_id].name),
    ):
        awaiting = ""
        if reprint_pending(status):
            cause = causes[status.id]
            who = causers.get(cause.user_id, "someone")
            what = "reopened" if cause.reopened else "changed after finalizing"
            awaiting = f"{format_time(cause.at, fmt='%b %d')} by {who} ({what})"
        rows.append(
            {
                "Section": sections[status.section_id].name,
                # The date itself, formatted by MONTH_COLUMN, so clicking
                # the header sorts by date rather than alphabetically.
                "Month": status.year_month,
                "Status": display_state(status.status) if status.status else DASH,
                # §33: a reopened month's printed SF2/SF4 may be stale.
                REPRINT_COLUMN: awaiting,
            }
        )
    return rows


# Supabase's free tier caps the database at 500 MB. Past it the project
# goes read-only, which for this app means no grade can be saved. Set
# DB_LIMIT_MB when the plan changes, so no code edit or redeploy is needed.
DB_LIMIT_MB = int(os.environ.get("DB_LIMIT_MB", "500"))
DB_WARN_FRACTION = 0.8
DB_CRITICAL_FRACTION = 0.9
DB_USAGE_TTL_SECONDS = 300
_MB = 1024 * 1024


@st.cache_data(ttl=DB_USAGE_TTL_SECONDS, show_spinner=False)
def _database_usage() -> tuple[int, list[tuple[str, int]]] | None:
    """(total bytes, [(table, bytes incl. indexes)] largest five), or None
    if the catalogs could not be read.

    Cached because the size moves slowly and every dashboard rerun would
    otherwise pay two round trips for it. A failure is returned as None
    rather than raised, because an exception is not cached and a down
    database would be retried on every click. Nothing here is per-viewer,
    so sharing the cache across users is safe. The session is opened
    inside, since a Session cannot cross the cache boundary.

    The total covers every schema, so the table list does too: Supabase's
    own auth and storage tables count against the same quota.
    """
    try:
        with get_session() as session:
            total = session.execute(text("SELECT pg_database_size(current_database())")).scalar()
            tables = session.execute(
                text(
                    "SELECT n.nspname, c.relname, pg_total_relation_size(c.oid) "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.relkind = 'r' "
                    "AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
                    "ORDER BY 3 DESC LIMIT 5"
                )
            ).all()
    except Exception:
        logger.exception("Could not read database size")
        return None
    return int(total), [
        (name if schema == "public" else f"{schema}.{name}", int(size))
        for schema, name, size in tables
    ]


def _render_database_usage() -> None:
    """A card showing how much of the free-tier database is used."""
    usage = _database_usage()
    if usage is None:
        st.caption("Database size is unavailable right now.")
        return
    total, tables = usage

    used_mb = total / _MB
    fraction = used_mb / DB_LIMIT_MB
    with st.container(border=True):
        st.markdown("**Database space**")
        st.metric("Used", f"{used_mb:,.1f} MB")
        st.progress(min(fraction, 1.0), text=f"{fraction:.0%} of {DB_LIMIT_MB} MB")
        if fraction >= DB_CRITICAL_FRACTION:
            st.error(
                "The database is nearly full. At the limit Supabase makes it "
                "read-only and no grade can be saved. Archive old audit entries "
                "or upgrade the plan.",
                icon="🚨",
            )
        elif fraction >= DB_WARN_FRACTION:
            st.warning(
                f"The database is over {DB_WARN_FRACTION:.0%} of its {DB_LIMIT_MB} MB limit.",
                icon="⚠️",
            )
        if tables:
            st.caption(
                "Largest tables: "
                + ", ".join(f"{name} {size / _MB:,.1f} MB" for name, size in tables)
            )
        st.caption(f"Refreshed at most every {DB_USAGE_TTL_SECONDS // 60} minutes.")


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "SCHOOL_HEAD")
    st.title("Dashboard")
    st.caption(
        "This page is for viewing data only."
    )
    render_flashes()

    # Infrastructure, not school data: only the administrator can act on it.
    # Above the school-year picker and its early returns, since the size is
    # of the whole database and must show even for a year with no sections.
    if "SUPER_ADMIN" in current_user.role_codes:
        _render_database_usage()

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
        )

        sections = _enrollment_overview(session, sy_choice)
        if not sections:
            st.info("No sections for this school year yet.")
            return

        total_active = sum(row["Active"] for row in sections)
        col1, col2, col3 = st.columns(3)
        col1.metric("Sections", len(sections))
        col2.metric("Active learners", f"{total_active:,}")
        col3.metric(
            "Records complete",
            sum(
                int(str(row["Records complete"]).split("/")[0])
                for row in sections
                if row["Records complete"] != DASH
            ),
        )

        gender_rows = _gender_breakdown(session, sy_choice)
        if gender_rows:
            _render_gender_breakdown(gender_rows)

        st.divider()
        st.subheader("Sections")
        _render_sections(sections)

        st.divider()
        st.subheader("Grade encoding")
        progress = _encoding_progress(session, sy_choice)
        if progress:
            st.dataframe(pd.DataFrame(progress), hide_index=True, width="stretch")
            st.caption(
                "A term shows CLOSED when teachers cannot currently encode grades "
                "for it. That is the normal state between encoding periods."
            )
        else:
            st.caption("This school year has no terms yet.")

        st.divider()
        st.subheader("Attendance months")
        attendance = _attendance_status(session, sy_choice)
        if attendance:
            outstanding = [r for r in attendance if r["Status"] != FinalizationState.FINALIZED.value]
            if outstanding:
                st.caption(f"{len(outstanding)} section-month(s) not yet finalized.")
            awaiting = [r for r in attendance if r[REPRINT_COLUMN]]
            if awaiting:
                st.warning(
                    f"{len(awaiting)} section-month(s) were reopened after being "
                    "finalized and are still waiting on a reprint. An SF2 or SF4 printed "
                    "before the reopen may be out of date — the adviser or registrar "
                    "confirms the new copy on the Attendance & SF2 or SF2 page.",
                    icon="🖨️",
                )
            st.dataframe(
                pd.DataFrame(attendance),
                hide_index=True,
                width="stretch",
                column_config={"Month": MONTH_COLUMN},
            )
        else:
            st.caption("No attendance months have been started yet.")

        if current_user.is_read_only():
            st.divider()
            st.caption(
                "You have read-only access. Reports can be viewed and printed from "
                "the SF9, SF2 and Term Cards pages."
            )
