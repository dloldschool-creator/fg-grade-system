import calendar as _calendar
import uuid
from datetime import date, datetime, timezone

import pandas as pd
import streamlit as st
from sqlalchemy import select, union_all

from app import audit_service
from app.admin_pages._helpers import (
    _forget_stale,
    clear_text_fields,
    flash,
    generation_key,
    get_session,
    preselect_section,
    render_flashes,
    render_reprint_notice,
    section_picker,
    try_commit,
)
from app.attendance_service import (
    ENCODED_DAYS_OBJECT,
    bump_version,
    class_days_in_month,
    day_bit,
    display_state,
    encoded_masks,
    finalize_month,
    get_month_status,
    get_or_create_month_status,
    mark_encoded,
    mark_unencoded,
    month_of,
    months_with_class_days,
    records_for_month,
    reopen_month,
    roster_for_month,
    statuses_for_days,
    summarize_month_batch,
    validate_month,
)
from app.auth import require_role
from app.display_time import SCHOOL_TZ, format_time
from app.models.admin import AuditLog
from app.models.attendance import AttendanceEncodedDays, AttendanceRecord
from app.models.enums import AttendanceStatus, FinalizationState
from app.models.organization import SchoolYear
from app.models.rbac import User
from app.section_access import may_reopen_attendance_month
from app.sf2_download import render_sf2_downloads
from app.text_rules import REASON_MAX, clean_text, reason_problem

# §30's printed codes. The DB stores the internal enum; these are only the
# encoding UI's shorthand, and the SF2 renderer (Phase 9) does its own
# mapping — notably PRESENT prints as a blank cell on the paper form, but
# showing it blank *here* would make "encoded as present" and "nobody has
# touched this yet" look identical.
CODE_BY_STATUS = {
    AttendanceStatus.PRESENT: "P",
    AttendanceStatus.ABSENT: "X",
    AttendanceStatus.LATE: "T-L",
    AttendanceStatus.CUTTING: "T-C",
}
STATUS_BY_CODE = {code: status for status, code in CODE_BY_STATUS.items()}
# audit_service.jsonable() serialises the enum to its (identical) .value
# string, so a payload's "status" reads back as e.g. "ABSENT" rather than
# the enum member — this maps that string straight to the printed code.
CODE_BY_STATUS_VALUE = {status.value: code for status, code in CODE_BY_STATUS.items()}

# Shown for a day outside the learner's active window (before a late
# enrollee arrived, after a transfer-out). Not editable in any meaningful
# sense — anything typed into such a cell is ignored on save.
NOT_ELIGIBLE = "·"

LEARNER_COLUMN = "Learner"
SEX_COLUMN = "Sex"

# FOR_REVIEW is retired (spec §33) but must stay here: months already marked
# keep that value in the database, and dropping it would lock them read-only
# with no Reopen path (reopen_month only accepts FINALIZED).
EDITABLE_STATES = {FinalizationState.NOT_STARTED, FinalizationState.OPEN, FinalizationState.FOR_REVIEW}


def _naive_utc(value: datetime) -> datetime:
    """Normalises a tz-aware or tz-naive timestamp for comparison.

    `AuditLog.created_at` and `AttendanceMonthStatus.last_saved_at` are
    both TIMESTAMP WITHOUT TIME ZONE columns, so a value just read back
    from Postgres is naive — but a value set on an object in the current
    session and not yet reloaded (e.g. right after `_save_grid` stamps
    it) is still the tz-aware `datetime.now(timezone.utc)` that wrote it.
    Same mismatch `analytics_service._is_stale` normalises for award
    staleness; comparing the two directly without this raises TypeError.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _last_attendance_change(session, roster, class_days) -> AuditLog | None:
    """Most recent ATTENDANCE_CHANGED entry for this month's records, or
    None if nothing has ever overridden a day (seeding to PRESENT is
    deliberately not logged — see `_save_grid`'s comment — so a month
    that's only ever been prepared, never edited, returns None here).
    Clearing a saved P is logged against the month's
    AttendanceEncodedDays row, so those ids are searched too."""
    enrollment_ids = [e.id for e, _, _ in roster]
    class_day_ids = [d.id for d in class_days]
    if not enrollment_ids or not class_day_ids:
        return None
    record_ids = [
        row[0]
        for row in session.execute(
            union_all(
                select(AttendanceRecord.id).where(
                    AttendanceRecord.enrollment_id.in_(enrollment_ids),
                    AttendanceRecord.calendar_date_id.in_(class_day_ids),
                ),
                select(AttendanceEncodedDays.id).where(
                    AttendanceEncodedDays.enrollment_id.in_(enrollment_ids),
                    AttendanceEncodedDays.year_month.in_(
                        {month_of(d.calendar_date) for d in class_days}
                    ),
                ),
            )
        ).all()
    ]
    if not record_ids:
        return None
    return (
        session.query(AuditLog)
        .filter(
            AuditLog.action == audit_service.ATTENDANCE_CHANGED,
            AuditLog.object_id.in_(record_ids),
        )
        .order_by(AuditLog.created_at.desc())
        .first()
    )


# Shown boldly right by the "Save attendance" button (where an adviser is
# actually looking right after editing) and also folded into
# `_blocking_reasons` so it still gates the Finalize button — one string so
# the two places can't drift apart. `_finalization_panel` skips re-printing
# it as a caption since the bold version above has already said it.
UNSAVED_EDITS_MESSAGE = (
    "There are unsaved edits in the grid above — click **Save attendance** first."
)


def unsaved_defaults_message(count: int) -> str:
    return (
        f"{count} day(s) are showing **P** (present) but aren't saved yet — "
        "click **Save attendance** to record them."
    )


def _cell_code(value) -> str:
    """A grid cell as an upper-case code, with a cleared cell as "".

    Deleting a cell's content in `st.data_editor` hands back None, and
    pandas can turn that into NaN — `str(nan)` is "nan", which surfaced as
    "Ignored unrecognised code(s) … 'NAN'" the first time someone cleared
    a row of prepared days."""
    if value is None or pd.isna(value):
        return ""
    return str(value).strip().upper()


def _blocking_reasons(
    report: dict,
    has_unsaved_edits: bool,
    current_state: FinalizationState,
    class_days,
    today,
    pending_defaults: int,
) -> list[str]:
    """The Finalize button's gate, split out from `_finalization_panel` so
    it's testable without a Streamlit runtime. An empty list means
    finalizing is allowed; each string is shown to the adviser as its own
    caption so they know exactly what's outstanding."""
    reasons: list[str] = []
    if report["problems"]:
        reasons.append("Resolve everything in red above before finalizing.")
    if has_unsaved_edits:
        reasons.append(UNSAVED_EDITS_MESSAGE)
    if pending_defaults:
        reasons.append(unsaved_defaults_message(pending_defaults))
    # `current_state` is deliberately not a gate: the first Save moves a
    # month out of NOT_STARTED, and a month filled in by hand before that
    # existed must be finalizable without a no-op Save.
    last_class_day = class_days[-1].calendar_date if class_days else None
    if last_class_day is not None and last_class_day >= today:
        reasons.append(
            f"This month is still in progress — its last class day is "
            f"{last_class_day:%B %d, %Y}. Finalize once the month has ended."
        )
    return reasons


def _build_grid(
    session, roster, class_days, default_through: date | None = None
) -> tuple[pd.DataFrame, dict]:
    """The grid plus `{enrollment_id: n}` of cells showing a *default* P.

    With `default_through` set, an eligible day on or before it that has no
    record yet shows **P** instead of blank. Save then writes it, which is
    what the old "Prepare / refresh this month's sheet" button did, minus
    the button. Days after it stay blank (they haven't happened). A default
    is only shown; nothing is written until Save, so merely viewing a
    month never inserts rows.
    """
    statuses = statuses_for_days(session, [e.id for e, _, _ in roster], class_days)
    rows = []
    pending: dict = {}
    for enrollment, learner, window in roster:
        row = {
            LEARNER_COLUMN: f"{learner.last_name}, {learner.first_name}",
            SEX_COLUMN: learner.sex.value[0],
        }
        for day in class_days:
            column = str(day.calendar_date.day)
            if not window.contains(day.calendar_date):
                row[column] = NOT_ELIGIBLE
                continue
            status = statuses.get((enrollment.id, day.id))
            if status is not None:
                row[column] = CODE_BY_STATUS[status]
            elif default_through is not None and day.calendar_date <= default_through:
                row[column] = CODE_BY_STATUS[AttendanceStatus.PRESENT]
                pending[enrollment.id] = pending.get(enrollment.id, 0) + 1
            else:
                row[column] = ""
        rows.append(row)
    return pd.DataFrame(rows), pending


def _save_grid(
    session, section_id, school_year_id, year, month, roster, class_days, edited: pd.DataFrame, user_id
) -> bool:
    """Returns True only if something was actually committed. On a real
    change, also stamps `AttendanceMonthStatus.last_saved_at/by` in the
    same commit — the finalization panel's "changed after your last save"
    check reads that column, not "a save happened at all," so a no-op
    save must not move it."""
    enrollment_ids = [e.id for e, _, _ in roster]
    records = records_for_month(session, enrollment_ids, [d.id for d in class_days])
    masks = encoded_masks(session, enrollment_ids, {month_of(d.calendar_date) for d in class_days})
    changed = 0
    invalid: list[str] = []
    # A saved P is a bit in AttendanceEncodedDays, not a row (2026-10-29):
    # every first Save of a month would otherwise insert one row per
    # learner per day. `encode`/`unencode` collect `{(enrollment_id,
    # year_month): bits}` and go in as two bitwise statements at the end,
    # so two people saving the same month at once each add their days —
    # the job ON CONFLICT DO NOTHING did for the old P rows. An X, T-L or
    # T-C still goes through the ORM, so a genuine clash (two people typing
    # X and T-L) still surfaces.
    encode: dict = {}
    unencode: dict = {}
    for index, (enrollment, learner, window) in enumerate(roster):
        for day in class_days:
            column = str(day.calendar_date.day)
            if not window.contains(day.calendar_date):
                continue
            raw = _cell_code(edited.iloc[index][column])
            if raw == NOT_ELIGIBLE:
                continue
            month_key = (enrollment.id, month_of(day.calendar_date))
            bit = day_bit(day.calendar_date)
            mask_id, mask = masks.get(month_key, (None, 0))
            saved_present = bool(mask & bit)
            record = records.get((enrollment.id, day.id))
            if raw == "":
                # Clearing a day that has been saved takes it back to "not
                # encoded" — the only way to undo the old Prepare button's
                # Present marks on days that hadn't happened. On a past day
                # the grid then offers its default P again. Audited like any
                # other change, since it alters what the day said. A plain P
                # has no record, so it is logged against its month's row.
                if record is not None or saved_present:
                    audit_service.record(
                        session,
                        action=audit_service.ATTENDANCE_CHANGED,
                        object_type="attendance_records" if record is not None else ENCODED_DAYS_OBJECT,
                        object_id=record.id if record is not None else mask_id,
                        user_id=user_id,
                        previous={
                            "status": record.status if record is not None else AttendanceStatus.PRESENT
                        },
                        new={
                            "status": None,
                            "learner": f"{learner.last_name}, {learner.first_name}",
                            "date": day.calendar_date,
                        },
                    )
                    if record is not None:
                        session.delete(record)
                    unencode[month_key] = unencode.get(month_key, 0) | bit
                    changed += 1
                continue
            status = STATUS_BY_CODE.get(raw)
            if status is None:
                invalid.append(f"{learner.last_name} on {day.calendar_date:%b %d}: '{raw}'")
                continue
            if record is None and status == AttendanceStatus.PRESENT:
                # Almost always a grid default being saved. Not audited — a
                # first P is the initial entry, and logging it would bury
                # every month's real corrections under hundreds of rows.
                if not saved_present:
                    encode[month_key] = encode.get(month_key, 0) | bit
                    changed += 1
            elif record is None:
                # A first X, T-L or T-C is audited even though it is a
                # create: the grid showed the day as P (saved or default),
                # so this is the change from present that §50 cares about.
                new_record = AttendanceRecord(
                    id=uuid.uuid4(),
                    enrollment_id=enrollment.id,
                    calendar_date_id=day.id,
                    status=status,
                    encoded_by_user_id=user_id,
                )
                session.add(new_record)
                encode[month_key] = encode.get(month_key, 0) | bit
                changed += 1
                audit_service.record(
                    session,
                    action=audit_service.ATTENDANCE_CHANGED,
                    object_type="attendance_records",
                    object_id=new_record.id,
                    user_id=user_id,
                    previous={"status": AttendanceStatus.PRESENT if saved_present else "not yet encoded"},
                    new={
                        "status": status,
                        "learner": f"{learner.last_name}, {learner.first_name}",
                        "date": day.calendar_date,
                    },
                )
            elif record.status != status:
                # Including a change back to P: the record stays, as a P, so
                # the audit entries about this day still point at a row.
                previous_status = record.status
                record.status = status
                record.encoded_by_user_id = user_id
                record.version += 1
                changed += 1
                # §50 asks for attendance "altered after initial entry" —
                # so the create branch above isn't logged (that IS the
                # initial entry), only a change to what a day already said.
                audit_service.record(
                    session,
                    action=audit_service.ATTENDANCE_CHANGED,
                    object_type="attendance_records",
                    object_id=record.id,
                    user_id=user_id,
                    previous={"status": previous_status},
                    new={
                        "status": status,
                        "learner": f"{learner.last_name}, {learner.first_name}",
                        "date": day.calendar_date,
                    },
                )

    if invalid:
        flash(
            "error",
            "Ignored unrecognised code(s) — use P, X, T-L or T-C: " + "; ".join(invalid[:5]),
        )
    mark_unencoded(session, unencode, user_id)
    mark_encoded(session, encode, user_id)
    if changed:
        row = get_or_create_month_status(session, section_id, school_year_id, year, month)
        row.last_saved_by_user_id = user_id
        row.last_saved_at = datetime.now(timezone.utc)
        if row.status == FinalizationState.NOT_STARTED:
            row.status = FinalizationState.OPEN
        bump_version(row)
        return try_commit(session, f"Saved {changed} attendance change(s).")
    session.rollback()
    flash("info", "No changes to save.")
    return False


def _summary_table(session, roster, class_days, pending_by_enrollment: dict) -> None:
    rows = []
    summaries = summarize_month_batch(session, roster, class_days)
    for enrollment, learner, window in roster:
        summary = summaries[enrollment.id]
        # A default P on the grid has no record yet, so the engine counts it
        # as unencoded; split it out so this table agrees with the grid.
        unsaved = pending_by_enrollment.get(enrollment.id, 0)
        rows.append(
            {
                "Learner": f"{learner.last_name}, {learner.first_name}",
                "Eligible days": summary.eligible_days,
                "Present": summary.days_present,
                "Absent": summary.days_absent,
                "Late": summary.late_count,
                "Cutting": summary.cutting_count,
                "Not encoded": summary.unencoded_days - unsaved,
                "P not saved": unsaved,
                "5+ absences": "⚠️" if summary.has_consecutive_absence_warning else "",
            }
        )
    st.table(rows)


def _finalization_panel(
    session, current_user, section, school_year_id, year, month, roster, class_days, has_unsaved_edits,
    pending_by_enrollment: dict,
) -> None:
    section_id = section.id
    status = get_month_status(session, section_id, year, month)
    current_state = status.status if status else FinalizationState.NOT_STARTED

    st.write(f"**Month status:** {display_state(current_state)}")

    render_reprint_notice(session, current_user, section, status)

    if status is not None and status.status == FinalizationState.FINALIZED:
        st.success(
            f"Finalized {format_time(status.finalized_at)} — attendance is read-only."
        )
        if may_reopen_attendance_month(current_user, section):
            with st.form(f"reopen_att_{status.id}"):
                reason = st.text_area(
                    "Reopen reason (required)",
                    max_chars=REASON_MAX,
                    placeholder="e.g. Marked DELA CRUZ absent on Sep 12 by mistake",
                )
                if st.form_submit_button("Reopen"):
                    problem = reason_problem(reason)
                    if problem:
                        st.error(problem)
                    else:
                        reason = clean_text(reason)
                        try:
                            reopen_month(session, status, current_user.id, reason)
                        except ValueError as exc:
                            session.rollback()
                            flash("error", str(exc))
                            st.rerun()
                        audit_service.record(
                            session,
                            action=audit_service.ATTENDANCE_MONTH_REOPENED,
                            object_type="attendance_month_status",
                            object_id=status.id,
                            user_id=current_user.id,
                            previous={"status": FinalizationState.FINALIZED},
                            new={"status": status.status, "year": year, "month": month},
                            reason=reason,
                        )
                        try_commit(session, "Reopened — attendance is editable again.")
                        st.rerun()
        else:
            st.caption(
                "Only this section's adviser, a registrar or an admin can reopen a "
                "finalized month."
            )
        return

    today = datetime.now(SCHOOL_TZ).date()
    report = validate_month(
        session, section_id, school_year_id, year, month,
        through=today, pending_by_enrollment=pending_by_enrollment,
    )
    pending_defaults = sum(pending_by_enrollment.values())

    col1, col2, col3 = st.columns(3)
    col1.metric("Learners on this sheet", report["roster_size"])
    col2.metric("Class days", report["class_day_count"])
    col3.metric(
        "Male / Female",
        f"{report['sex_totals'].get('MALE', 0)} / {report['sex_totals'].get('FEMALE', 0)}",
    )
    if report["movement_totals"]:
        st.caption(
            "Movements on record: "
            + ", ".join(f"{k} ×{v}" for k, v in sorted(report["movement_totals"].items()))
        )

    for warning in report["warnings"]:
        st.warning(warning)
    for problem in report["problems"][:20]:
        st.error(problem)
    if len(report["problems"]) > 20:
        st.error(f"…and {len(report['problems']) - 20} more.")

    blocking_reasons = _blocking_reasons(
        report, has_unsaved_edits, current_state, class_days, today, pending_defaults
    )

    can_finalize = not blocking_reasons
    for reason in blocking_reasons:
        if reason == UNSAVED_EDITS_MESSAGE or reason == unsaved_defaults_message(pending_defaults):
            continue  # already shown boldly by the Save attendance button
        st.caption(reason)

    last_change = _last_attendance_change(session, roster, class_days)
    if last_change is not None:
        changed_by = session.get(User, last_change.user_id) if last_change.user_id else None
        who = changed_by.full_name if changed_by else "someone" if last_change.user_id else "the system"
        payload = last_change.new_value or {}
        learner_name = payload.get("learner", "a learner")
        changed_date = payload.get("date", "")
        status_value = payload.get("status")
        code = "blank" if status_value is None else CODE_BY_STATUS_VALUE.get(status_value, status_value)
        when = format_time(last_change.created_at, fmt="%b %d, %Y %H:%M")

        last_saved_at = status.last_saved_at if status else None
        changed_after_save = (
            last_saved_at is not None
            and _naive_utc(last_change.created_at) > _naive_utc(last_saved_at)
        )
        if changed_after_save:
            saved_by = (
                session.get(User, status.last_saved_by_user_id)
                if status.last_saved_by_user_id
                else None
            )
            saver = saved_by.full_name if saved_by else "someone"
            saved_when = format_time(last_saved_at, fmt="%b %d, %Y %H:%M")
            st.warning(
                f"Attendance changed after the last save ({saver}, {saved_when}): "
                f"{who} set **{learner_name}** to **{code}** on {changed_date} at "
                f"{when}. Re-check the grid and re-save before finalizing.",
                icon="⚠️",
            )
        else:
            st.caption(
                f"Last override: {when} by {who} — {learner_name}, {changed_date} → {code}."
            )

    if st.button("Finalize month", disabled=not can_finalize, type="primary"):
        row = get_or_create_month_status(session, section_id, school_year_id, year, month)
        try:
            finalize_month(session, row, current_user.id)
        except ValueError as exc:
            session.rollback()
            flash("error", str(exc))
            st.rerun()
        audit_service.record(
            session,
            action=audit_service.ATTENDANCE_MONTH_FINALIZED,
            object_type="attendance_month_status",
            object_id=row.id,
            user_id=current_user.id,
            new={
                "status": row.status,
                "year": year,
                "month": month,
                "roster_size": report["roster_size"],
                "class_days": report["class_day_count"],
            },
        )
        try_commit(session, "Month finalized — attendance is now read-only.")
        st.rerun()


# The SF2 page's "go to Attendance" button leaves the school year, section and
# month here. Not in the widgets' own keys: Streamlit drops a keyed widget's
# state in any run that does not draw it, so what is set on the SF2 page is
# gone by the time this page runs.
HANDOFF_KEY = "attendance_handoff"
PICKER_KEY = "attendance"
SCHOOL_YEAR_KEY = "attendance_school_year"
MONTH_KEY = "attendance_month"


def _apply_handoff() -> None:
    """Start the pickers on what the SF2 page had selected, once."""
    handoff = st.session_state.pop(HANDOFF_KEY, None)
    if not handoff:
        return
    st.session_state[SCHOOL_YEAR_KEY] = handoff["school_year_id"]
    preselect_section(PICKER_KEY, handoff["section_id"])
    st.session_state[MONTH_KEY] = handoff["month"]


def _print_sf2(section, school_year_id, year: int, month: int, state: FinalizationState) -> None:
    """The month's SF2, offered only once it is finalized.

    This is where advisers print it — the SF2 page is not in their sidebar
    (2026-10-17). In the Excel workbook the SF2 sheet was where attendance
    was typed, so advisers went to the SF2 page to encode and printed
    whatever was there. Encoding, finalizing and printing on one page, in
    that order, leaves no second page to mistake for the first. Registrars
    and the School Head can still print an unfinalized month from the SF2
    page, with a warning."""
    if state != FinalizationState.FINALIZED:
        st.info(
            "Finalize this month above and its SF2 can be downloaded here. Until "
            "then the figures can still change, so a printed copy could be out "
            "of date."
        )
        return
    st.caption(
        "Generated from the attendance you encoded above. If something changes "
        "after you print it, reopen the month, correct it, finalize again and "
        "print a new copy."
    )
    render_sf2_downloads(section, school_year_id, year, month)


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "ADVISER")
    st.title("Attendance & SF2")
    st.caption(
        "**P** present · **X** absent · **T-L** late · **T-C** cutting. Late and "
        f"cutting both count as present. A **{NOT_ELIGIBLE}** (dot) means the learner "
        "had not joined this class yet on that day, or had already left — those days "
        "can't be marked and are not counted as absences."
    )
    render_flashes()

    adviser_scoped = not current_user.has_role("SUPER_ADMIN", "REGISTRAR")
    _apply_handoff()

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
            key=SCHOOL_YEAR_KEY,
        )

        section = section_picker(
            session, sy_choice, key=PICKER_KEY,
            adviser_user_id=current_user.id if adviser_scoped else None,
        )
        if section is None:
            return
        section_choice = section.id

        months = months_with_class_days(session, sy_choice)
        if not months:
            st.warning(
                "No class days on the academic calendar for this school year — a Super "
                "Admin needs to generate it on the Academic Calendar page first."
            )
            return
        today = datetime.now(SCHOOL_TZ).date()
        try:
            default_month_index = months.index((today.year, today.month))
        except ValueError:
            default_month_index = 0
        _forget_stale(MONTH_KEY, months)
        month_choice = st.selectbox(
            "Month",
            options=months,
            format_func=lambda ym: f"{_calendar.month_name[ym[1]]} {ym[0]}",
            index=default_month_index,
            key=MONTH_KEY,
        )
        year, month = month_choice

        class_days = class_days_in_month(session, sy_choice, year, month)
        roster = roster_for_month(session, section_choice, sy_choice, year, month)
        if not roster:
            st.info("No learners on this section's sheet for this month.")
            return

        status = get_month_status(session, section_choice, year, month)
        current_state = status.status if status else FinalizationState.NOT_STARTED
        editable = current_state in EDITABLE_STATES

        st.caption(
            "Every class day up to today starts as **P** (present), so you only change "
            "the learners who were absent or late — then click **Save attendance**. "
            "Days that haven't happened yet stay blank."
        )

        st.divider()
        st.subheader(f"{_calendar.month_name[month]} {year} — {section.name}")

        dataframe, pending_by_enrollment = _build_grid(
            session, roster, class_days, default_through=today if editable else None
        )
        pending_defaults = sum(pending_by_enrollment.values())
        has_unsaved_edits = False
        if editable:
            edited = st.data_editor(
                dataframe,
                # Generation-carrying, so a successful save can drop the
                # editor's own copy of the edits (a cleared cell would
                # otherwise keep showing "None" over the reloaded grid).
                key=generation_key("attendance_grid", f"grid_{section_choice}_{year}_{month}"),
                disabled=[LEARNER_COLUMN, SEX_COLUMN],
                column_config={
                    LEARNER_COLUMN: st.column_config.TextColumn(width="medium", pinned=True),
                    SEX_COLUMN: st.column_config.TextColumn(width="small"),
                    **{
                        # NOT_ELIGIBLE has to be a valid option even though
                        # it's never a real choice — a SelectboxColumn cell
                        # holding a value outside its options renders empty,
                        # which would make an out-of-window day look like an
                        # un-encoded one.
                        str(d.calendar_date.day): st.column_config.SelectboxColumn(
                            str(d.calendar_date.day),
                            options=["", "P", "X", "T-L", "T-C", NOT_ELIGIBLE],
                            width="small",
                        )
                        for d in class_days
                    },
                },
                hide_index=True,
                width="stretch",
            )
            # A cleared cell comes back as None; compare it as the "" it means.
            has_unsaved_edits = not edited.apply(lambda c: c.map(_cell_code)).equals(
                dataframe.apply(lambda c: c.map(_cell_code))
            )
            if has_unsaved_edits:
                st.markdown(f"**:red[{UNSAVED_EDITS_MESSAGE}]**")
            if pending_defaults:
                st.markdown(f"**:red[{unsaved_defaults_message(pending_defaults)}]**")
            if st.button("Save attendance", type="primary"):
                if _save_grid(
                    session, section_choice, sy_choice, year, month,
                    roster, class_days, edited, current_user.id,
                ):
                    clear_text_fields("attendance_grid")
                st.rerun()
        else:
            st.dataframe(
                dataframe,
                hide_index=True,
                width="stretch",
                column_config={LEARNER_COLUMN: st.column_config.TextColumn(pinned=True)},
            )
            st.caption("This month is finalized — reopen it below to make changes.")

        st.divider()
        st.subheader("Monthly summary")
        _summary_table(
            session, roster, [d for d in class_days if d.calendar_date <= today], pending_by_enrollment
        )

        st.divider()
        st.subheader("Finalization")
        _finalization_panel(
            session, current_user, section, sy_choice, year, month,
            roster, class_days, has_unsaved_edits, pending_by_enrollment,
        )

        st.divider()
        st.subheader("Print SF2")
        _print_sf2(section, sy_choice, year, month, current_state)
