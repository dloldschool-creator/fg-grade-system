import calendar as _calendar
from datetime import date

import streamlit as st

from app import audit_service
from app.admin_pages._helpers import flash, get_session, render_flashes, try_commit
from app.attendance_service import (
    WORKBOOK_CLASS_DAY_TARGETS,
    class_days_in_month,
    generate_calendar,
    resequence_class_days,
)
from app.auth import require_role
from app.models.attendance import AcademicCalendarDate
from app.models.organization import SchoolYear, Term

WEEKDAY_NAMES = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


def _month_options(school_year: SchoolYear) -> list[tuple[int, int]]:
    """Every (year, month) the school year spans, whether or not it has
    class days yet — the calendar has to be viewable before it's
    generated."""
    months: list[tuple[int, int]] = []
    year, month = school_year.start_date.year, school_year.start_date.month
    while (year, month) <= (school_year.end_date.year, school_year.end_date.month):
        months.append((year, month))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return months


def _summary_table(session, school_year: SchoolYear) -> None:
    """Generated class-day counts per month against §28's workbook
    targets. The gap is where the admin still needs to mark the holidays
    that can't be derived from the year — proclaimed special non-working
    days (All Souls', Immaculate Conception), lunar ones (Eid'l Fitr,
    Eid'l Adha), and local suspensions."""
    rows = []
    total_actual = 0
    total_target = 0
    for year, month in _month_options(school_year):
        actual = len(class_days_in_month(session, school_year.id, year, month))
        target = WORKBOOK_CLASS_DAY_TARGETS.get((year, month))
        total_actual += actual
        if target is not None:
            total_target += target
        rows.append(
            {
                "Month": f"{_calendar.month_name[month]} {year}",
                "Class days": actual,
                "Planned class days": target if target is not None else "—",
                "Diff": (actual - target) if target is not None else "—",
            }
        )
    rows.append(
        {
            "Month": "TOTAL",
            "Class days": total_actual,
            "Planned class days": total_target or "—",
            "Diff": (total_actual - total_target) if total_target else "—",
        }
    )
    st.table(rows)
    st.caption(
        "**Diff** shows how this month compares with the school's planned number "
        "of class days. A number above 0 means some days are still ticked as "
        "class days that shouldn't be — usually a special non-working day or a "
        "local class suspension. Untick those days below. Weekends, Holy Week, "
        "National Heroes Day and the fixed national holidays are already handled "
        "for you."
    )


def _month_editor(session, school_year: SchoolYear, year: int, month: int, current_user) -> None:
    first = date(year, month, 1)
    last = date(year, month, _calendar.monthrange(year, month)[1])
    days = (
        session.query(AcademicCalendarDate)
        .filter(
            AcademicCalendarDate.school_year_id == school_year.id,
            AcademicCalendarDate.calendar_date >= first,
            AcademicCalendarDate.calendar_date <= last,
        )
        .order_by(AcademicCalendarDate.calendar_date)
        .all()
    )
    if not days:
        st.info("No calendar dates generated for this month yet.")
        return

    terms = {t.id: t for t in session.query(Term).filter_by(school_year_id=school_year.id).all()}

    st.caption(
        f"{sum(1 for d in days if d.is_default_class_day)} class day(s) this month. "
        "Changing whether a date is a class day requires a reason, which is saved "
        "with your name and the time."
    )

    for day in days:
        term = terms.get(day.term_id)
        is_weekend = day.day_of_week >= 6
        label_bits = [
            f"**{day.calendar_date:%b %d}** ({WEEKDAY_NAMES[day.day_of_week]})",
            term.name if term else "no term",
        ]
        if day.is_default_class_day:
            label_bits.append(f"class day #{day.class_day_sequence}")
        else:
            label_bits.append("non-class day")
        if day.note:
            label_bits.append(f"_{day.note}_")
        if day.is_override:
            label_bits.append("**(overridden)**")

        with st.expander(" · ".join(label_bits)):
            if is_weekend:
                st.caption(
                    "Weekend — normally not a class day. Mark it as one only for a "
                    "scheduled make-up class."
                )
            with st.form(f"cal_{day.id}"):
                is_class_day = st.checkbox(
                    "Class day", value=day.is_default_class_day, key=f"ccd_{day.id}"
                )
                is_final = st.checkbox(
                    "Final class day of the school year",
                    value=day.is_final_class_day,
                    key=f"cfd_{day.id}",
                )
                note = st.text_input(
                    "Reason / note", value=day.note or "", key=f"cn_{day.id}"
                )
                if st.form_submit_button("Save"):
                    changed_class_day = is_class_day != day.is_default_class_day
                    if changed_class_day and not note.strip():
                        st.error("A reason is required to change a date's class-day status.")
                    else:
                        day.is_default_class_day = is_class_day
                        day.is_final_class_day = is_final
                        day.note = note.strip() or None
                        if changed_class_day:
                            day.is_override = True
                            day.overridden_by_user_id = current_user.id
                            # §50 lists a calendar change as auditable, and
                            # the note doubles as the required reason —
                            # which is why the empty-note branch above
                            # refuses the save.
                            audit_service.record(
                                session,
                                action=audit_service.CALENDAR_DAY_CHANGED,
                                object_type="academic_calendar_dates",
                                object_id=day.id,
                                user_id=current_user.id,
                                previous={"is_class_day": not is_class_day},
                                new={"is_class_day": is_class_day, "date": day.calendar_date},
                                reason=note.strip(),
                            )
                            # Any flip renumbers the whole year — the
                            # sequence is a running count, so a gap here
                            # shifts every later day.
                            resequence_class_days(session, school_year.id)
                        try_commit(session, f"Updated {day.calendar_date:%b %d, %Y}.")
                        st.rerun()


def render() -> None:
    current_user = require_role("SUPER_ADMIN")
    st.title("Academic Calendar")
    st.caption(
        "The school calendar, one date at a time. Each date belongs to its own "
        "term, so a month can span two terms — September covers the end of Term 1 "
        "and the start of Term 2."
    )
    render_flashes()

    with get_session() as session:
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        if not school_years:
            st.warning("No school years yet.")
            return
        sy_by_id = {sy.id: sy for sy in school_years}
        sy_choice = st.selectbox(
            "School year", options=[sy.id for sy in school_years], format_func=lambda v: sy_by_id[v].name
        )
        school_year = sy_by_id[sy_choice]

        terms = session.query(Term).filter_by(school_year_id=sy_choice).all()
        if not terms:
            st.warning("This school year has no terms yet — add them on School Years & Terms first.")
            return

        existing_count = (
            session.query(AcademicCalendarDate).filter_by(school_year_id=sy_choice).count()
        )
        col1, col2 = st.columns([1, 3])
        with col1:
            if st.button("Generate calendar"):
                created, skipped = generate_calendar(session, sy_choice)
                if try_commit(
                    session,
                    f"Generated {created} date(s); left {skipped} existing date(s) untouched.",
                ):
                    st.rerun()
        with col2:
            st.caption(
                f"{existing_count} date(s) generated so far. Safe to re-run — existing "
                "dates (including your overrides) are never modified, only missing "
                "dates are added. Weekdays inside a term become class days; weekends "
                "and the fixed-date national holidays do not."
            )

        if existing_count == 0:
            st.info("Generate the calendar to get started.")
            return

        st.divider()
        _summary_table(session, school_year)

        st.divider()
        st.subheader("Adjust dates")
        months = _month_options(school_year)
        month_choice = st.selectbox(
            "Month",
            options=months,
            format_func=lambda ym: f"{_calendar.month_name[ym[1]]} {ym[0]}",
        )
        _month_editor(session, school_year, month_choice[0], month_choice[1], current_user)
