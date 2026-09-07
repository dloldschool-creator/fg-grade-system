from datetime import date, datetime, timezone
from decimal import Decimal

import streamlit as st
from sqlalchemy.exc import IntegrityError

from app import audit_service
from app.admin_pages._helpers import clear_text_fields, flash, generation_key, get_session, render_flashes
from app.auth import require_role
from app.grading_engine import round_half_up
from app.grading_service import recompute_enrollment_grades_batch
from app.models.academic_structure import Section
from app.models.enums import EnrollmentStatus, GradeEncodingStatus, GradeWorkflowStatus
from app.models.grades import TermGrade
from app.models.learners import Enrollment, Learner
from app.roster_order import learner_order_by
from app.models.organization import SchoolYear, Term
from app.models.subjects import SectionSubjectOffering, Subject, TeacherAssignment

# A learner still counted as actively in the section for grading purposes —
# excludes transferred-out/dropped/NLS/shifted-out/completed/graduated,
# same spirit as the SF2 "no longer appears as active" rule (§32).
ROSTER_STATUSES = {
    EnrollmentStatus.ENROLLED,
    EnrollmentStatus.LATE_ENROLLMENT,
    EnrollmentStatus.TRANSFERRED_IN,
    EnrollmentStatus.SHIFTED_IN,
}


def _round_grade(value: float | None) -> Decimal | None:
    """Official grades are always whole numbers (every Final Grade/GA
    formula in the spec rounds), so round what the teacher typed the same
    DepEd half-up way at the point of entry, rather than storing a
    fractional value that just happens to render rounded elsewhere."""
    if value is None:
        return None
    return round_half_up(Decimal(str(value)))


def days_past_deadline(deadline: date | None, today: date | None = None) -> int | None:
    """How many days late encoding is, or None when it isn't (or when the
    term has no deadline set).

    Kept separate from the drawing so the boundary is testable: on the
    deadline itself nothing is late, and the day after is one day late.
    """
    if deadline is None:
        return None
    overdue = ((today or date.today()) - deadline).days
    return overdue if overdue > 0 else None


def _deadline_banner(term) -> None:
    """Warns once the submission deadline has passed.

    `terms.submission_deadline` gates nothing — encoding is controlled
    only by the OPEN/CLOSED toggle, and a Super Admin may leave a term
    open well past its deadline on purpose. So this informs rather than
    blocks: a teacher who is late should know, without being stopped from
    doing the thing they are late with.
    """
    overdue = days_past_deadline(term.submission_deadline)
    if overdue is None:
        if term.submission_deadline:
            st.caption(
                f"Submission deadline for {term.name}: "
                f"{term.submission_deadline:%d %B %Y}."
            )
        return

    st.warning(
        f"**Past the submission deadline.** Grades for {term.name} were due "
        f"{term.submission_deadline:%d %B %Y} — {overdue} day"
        f"{'s' if overdue != 1 else ''} ago. Encoding is still open, so you can "
        "save and submit as normal, but let your school head know.",
        icon="⏰",
    )


def render() -> None:
    current_user = require_role("SUBJECT_TEACHER")
    st.title("Gradebook")
    render_flashes()

    with get_session() as session:
        assignments = (
            session.query(TeacherAssignment)
            .filter_by(teacher_user_id=current_user.id, is_active=True)
            .all()
        )
        if not assignments:
            st.info("You have no active teaching assignments yet — ask a Super Admin to assign you on the Teacher Assignments page.")
            return

        offering_by_id = {}
        label_by_offering_id = {}
        for assignment in assignments:
            offering = session.get(SectionSubjectOffering, assignment.section_subject_offering_id)
            section = session.get(Section, offering.section_id)
            subject = session.get(Subject, offering.subject_id)
            term = session.get(Term, offering.term_id)
            school_year = session.get(SchoolYear, offering.school_year_id)
            offering_by_id[offering.id] = offering
            label_by_offering_id[offering.id] = (
                f"{school_year.name} — {section.name} — {subject.official_name} — {term.name}"
            )

        offering_choice = st.selectbox(
            "Class",
            options=list(label_by_offering_id.keys()),
            format_func=lambda v: label_by_offering_id[v],
        )
        offering = offering_by_id[offering_choice]
        section = session.get(Section, offering.section_id)
        subject = session.get(Subject, offering.subject_id)
        term = session.get(Term, offering.term_id)

        if term.grade_encoding_status != GradeEncodingStatus.OPEN:
            st.warning(
                f"Grade encoding is CLOSED for {term.name} — ask a Super Admin to open it "
                "on the School Years & Terms page before you can enter grades."
            )
            return

        _deadline_banner(term)

        enrollments = (
            session.query(Enrollment)
            .filter_by(section_id=section.id, school_year_id=offering.school_year_id)
            .join(Learner, Learner.id == Enrollment.learner_id)
            .order_by(*learner_order_by(Learner))
            .all()
        )
        roster = [e for e in enrollments if e.enrollment_status in ROSTER_STATUSES]
        if not roster:
            st.info("No actively-enrolled learners in this section yet.")
            return

        # One query for the whole roster. The form loop below used to call
        # session.get(Learner, ...) per row — the join above orders the
        # query but doesn't load Learner objects, so that was a round trip
        # each, ~40 of them at 85ms on every keystroke-triggered rerun.
        learners = {
            learner.id: learner
            for learner in session.query(Learner)
            .filter(Learner.id.in_([e.learner_id for e in roster]))
            .all()
        }

        existing_grades = {
            g.enrollment_id: g
            for g in session.query(TermGrade)
            .filter_by(section_subject_offering_id=offering.id, term_id=term.id)
            .all()
        }

        # For the audit trail below — GRADE_CHANGED and GRADE_SUBMITTED read
        # naturally as "who" (teacher) and "what" (grade), but not "whose
        # record", since object_id is the term_grades row, not the learner.
        learner_names = {
            e.id: f"{learners[e.learner_id].last_name}, {learners[e.learner_id].first_name}"
            for e in roster
            if e.learner_id in learners
        }

        st.subheader(f"{subject.official_name} — {term.name}")
        st.caption("Leave a grade blank if it isn't ready yet. Never type 0 to mean that.")
        st.caption(
            "Already encoded and need it blank again — dropped, transferred, or "
            "entered in error? Tick **Blank** next to it and say why; the number "
            "box alone can't be cleared once it has a value."
        )

        st.caption(
            "You can still edit a grade after submitting — doing so puts it back to "
            "DRAFT, so remember to press Submit again. Once a grade is verified or "
            "finalized it locks; ask a Super Admin if one needs reopening."
        )

        with st.form("gradebook_form"):
            grade_inputs = {}
            for enrollment in roster:
                learner = learners.get(enrollment.learner_id)
                existing = existing_grades.get(enrollment.id)
                locked = existing is not None and existing.status in {
                    GradeWorkflowStatus.VERIFIED,
                    GradeWorkflowStatus.FINALIZED,
                }
                col1, col2, col3, col4, col5 = st.columns([3, 1.6, 1.1, 2.4, 1.6])
                col1.write(f"{learner.last_name}, {learner.first_name}" if learner else "?")
                if locked:
                    col2.write(
                        f"{int(existing.official_grade)}" if existing.official_grade is not None else "—"
                    )
                    col5.caption(existing.status.value)
                else:
                    # 60-100 mirrors the seeded default grading policy's
                    # min/max (app/seed.py) — not resolved per-offering
                    # policy version yet; that's Phase 6 territory.
                    # Keyed by offering+enrollment, not just enrollment —
                    # a bare enrollment.id key would be reused as the
                    # teacher switches between classes for the same
                    # learner, since Streamlit widgets keep whatever
                    # value is already in session_state for a key instead
                    # of re-reading `value=` on every rerun. That was
                    # bleeding one subject's typed/saved grade into
                    # another subject's field for the same learner.
                    # format="%.0f" + step=1.0: official grades are always
                    # whole numbers (§18 and friends all ROUND()); typed
                    # values still get explicitly re-rounded at save time
                    # below rather than trusting the widget alone.
                    # generation_key here too, not just on the checkbox
                    # below: a cleared grade must render as a genuinely
                    # blank box next time, not the last-typed number the
                    # widget would otherwise keep showing from its own
                    # frontend state (see the note on the checkbox key).
                    number_value = col2.number_input(
                        "Grade",
                        min_value=60.0,
                        max_value=100.0,
                        value=float(existing.official_grade) if existing and existing.official_grade is not None else None,
                        step=1.0,
                        format="%.0f",
                        key=generation_key(f"gradebook_{offering.id}", f"grade_{enrollment.id}"),
                        label_visibility="collapsed",
                    )
                    has_grade = existing is not None and existing.official_grade is not None
                    # A number_input rendered with a real starting value can
                    # never be typed back to blank — Streamlit only makes a
                    # widget clearable when it *first* renders with
                    # value=None (see NumberInputSerde.deserialize: an empty
                    # submission falls back to the widget's original default
                    # rather than None). So a learner who drops out, transfers,
                    # or was graded in error has no way back to "not yet
                    # encoded" through the box itself — this checkbox is the
                    # only path, and it wins over whatever the box shows.
                    # generation_key, not a bare f-string: a checkbox inside
                    # st.form keeps its checked state in the *frontend* too,
                    # so popping session_state alone leaves the box still
                    # showing ticked after the rerun (the same trap
                    # clear_text_fields exists for). A fresh key after a
                    # successful clear is the only reset that reaches the
                    # browser — see the reset below, after commit.
                    clear = (
                        col3.checkbox(
                            "Blank",
                            key=generation_key(f"gradebook_{offering.id}", f"clear_{enrollment.id}"),
                            help="Clear this grade back to not-yet-encoded.",
                        )
                        if has_grade
                        else False
                    )
                    # Always rendered (not only once ticked): a checkbox
                    # inside st.form doesn't trigger a rerun on its own, so
                    # there is no live moment to reveal this field after the
                    # tick — it has to already be there for Save to read.
                    # Required only if the tick is on; enforced at Save,
                    # below, so the empty case can point back at the learner
                    # by name instead of failing silently.
                    reason = (
                        col4.text_input(
                            "Reason",
                            key=generation_key(f"gradebook_{offering.id}", f"reason_{enrollment.id}"),
                            placeholder="Reason for blanking (required if ticked)",
                            label_visibility="collapsed",
                        )
                        if has_grade
                        else ""
                    )
                    grade_inputs[enrollment.id] = (number_value, clear, reason)
                    col5.caption(existing.status.value.lower() if existing else "not yet encoded")

            save = st.form_submit_button("Save grades")
            submit = st.form_submit_button("Submit all draft grades")

            if save:
                # §50: blanking is the one edit here with no other trace of
                # *why* — a typed-over grade still has the old number in the
                # audit log, but a blank tells you nothing on its own about
                # whether it's a dropout, a transfer, or a typo undone. Checked
                # before anything is written, so a missing reason blocks the
                # whole save rather than silently skipping just that row.
                missing_reason = [
                    learner_names.get(enrollment_id, "?")
                    for enrollment_id, (_raw_value, clear, reason) in grade_inputs.items()
                    if clear and not (reason or "").strip()
                ]
                if missing_reason:
                    flash(
                        "error",
                        "Ticked Blank but no reason given for: "
                        + ", ".join(missing_reason)
                        + ". Fill in why before saving.",
                    )
                    st.rerun()

                changed = 0
                reverted = 0
                touched_enrollment_ids = []
                # (row, action, previous, new, reason) — recorded after one
                # flush below, since a brand-new row has no id until then.
                pending_audits = []
                for enrollment_id, (raw_value, clear, reason) in grade_inputs.items():
                    # The checkbox wins over the box: it's the only way to
                    # actually reach None once a grade has been typed in,
                    # so a ticked box means blank no matter what the
                    # (unclearable) number_input still displays.
                    grade_value = None if clear else _round_grade(raw_value)
                    existing = existing_grades.get(enrollment_id)
                    if existing is None:
                        if grade_value is None:
                            continue  # nothing entered, nothing to create
                        created = TermGrade(
                            enrollment_id=enrollment_id,
                            section_subject_offering_id=offering.id,
                            term_id=term.id,
                            official_grade=grade_value,
                            status=GradeWorkflowStatus.DRAFT,
                        )
                        session.add(created)
                        pending_audits.append(
                            (created, audit_service.GRADE_CREATED, None, {"official_grade": grade_value}, None)
                        )
                        changed += 1
                        touched_enrollment_ids.append(enrollment_id)
                    elif existing.official_grade != grade_value:
                        previous = {
                            "official_grade": existing.official_grade,
                            "status": existing.status,
                            "section": section.name,
                            "learner": learner_names.get(enrollment_id),
                        }
                        existing.official_grade = grade_value
                        if existing.status == GradeWorkflowStatus.SUBMITTED:
                            existing.status = GradeWorkflowStatus.DRAFT
                            reverted += 1
                        existing.version += 1
                        pending_audits.append(
                            (
                                existing,
                                audit_service.GRADE_CHANGED,
                                previous,
                                {"official_grade": grade_value, "status": existing.status},
                                reason.strip() if clear else None,
                            )
                        )
                        changed += 1
                        touched_enrollment_ids.append(enrollment_id)
                try:
                    if pending_audits:
                        session.flush()
                        for row, action, previous, new, audit_reason in pending_audits:
                            audit_service.record(
                                session,
                                action=action,
                                object_type="term_grades",
                                object_id=row.id,
                                user_id=current_user.id,
                                previous=previous,
                                new=new,
                                reason=audit_reason,
                            )
                    session.commit()
                    recompute_enrollment_grades_batch(session, touched_enrollment_ids)
                    if any(clear for _raw_value, clear, _reason in grade_inputs.values()):
                        # Otherwise a cleared checkbox (and its reason box)
                        # stays exactly as typed — in the browser, not just
                        # session_state — after its row goes back to "not yet
                        # encoded" and stops rendering, and both come back
                        # pre-filled the moment the teacher types a new grade
                        # in for that learner, silently re-blanking it again.
                        clear_text_fields(f"gradebook_{offering.id}")
                    message = f"Saved ({changed} updated)." if changed else "No changes to save."
                    if reverted:
                        message += f" {reverted} reverted to DRAFT for re-submission."
                    flash("success", message)
                except IntegrityError:
                    session.rollback()
                    flash("error", "Couldn't save — please try again.")
                st.rerun()

            if submit:
                now = datetime.now(timezone.utc)
                submitted_count = 0
                touched_enrollment_ids = []
                for enrollment_id in [e.id for e in roster]:
                    existing = existing_grades.get(enrollment_id)
                    if existing is not None and existing.status == GradeWorkflowStatus.DRAFT:
                        existing.status = GradeWorkflowStatus.SUBMITTED
                        existing.submitted_by_user_id = current_user.id
                        existing.submitted_at = now
                        existing.version += 1
                        submitted_count += 1
                        touched_enrollment_ids.append(enrollment_id)
                        audit_service.record(
                            session,
                            action=audit_service.GRADE_SUBMITTED,
                            object_type="term_grades",
                            object_id=existing.id,
                            user_id=current_user.id,
                            previous={
                                "status": GradeWorkflowStatus.DRAFT,
                                "section": section.name,
                                "learner": learner_names.get(enrollment_id),
                            },
                            new={
                                "status": GradeWorkflowStatus.SUBMITTED,
                                "official_grade": existing.official_grade,
                            },
                        )
                session.commit()
                recompute_enrollment_grades_batch(session, touched_enrollment_ids)
                flash(
                    "success",
                    f"Submitted {submitted_count} grade(s). They're locked here until an "
                    "adviser/admin verifies or reopens them.",
                )
                st.rerun()
