import streamlit as st

from app.admin_pages._helpers import get_session, render_flashes
from app.auth import require_role
from app.models.academic_structure import GradeLevel, Section
from app.models.grades import TermGradeSummary
from app.models.learners import Enrollment, Learner
from app.models.organization import School, SchoolYear, Term
from app.models.rbac import User
from app.report_card import build_term_subject_rows, load_report_context
from app.term_card import (
    CARDS_PER_PAGE,
    TermCardData,
    generate_term_cards,
    page_count,
)

DASH = "—"


def _fmt(value):
    return int(value) if value is not None else DASH


def _card_for(session, enrollment, learner, *, school, term, grade_level, section, adviser,
              context=None, summary=None) -> TermCardData:
    comment = {
        1: enrollment.term1_adviser_comment,
        2: enrollment.term2_adviser_comment,
        3: enrollment.term3_adviser_comment,
    }.get(term.term_number)
    return TermCardData(
        school_name=school.school_name if school else "",
        term_name=term.name,
        learner_name=f"{learner.last_name}, {learner.first_name}",
        lrn=learner.lrn or "",
        grade_level=(grade_level.code or grade_level.name) if grade_level else "",
        section_name=section.name if section else "",
        subjects=build_term_subject_rows(session, enrollment, term.term_number, context),
        term_average=summary.term_average if summary else None,
        adviser_name=adviser.full_name if adviser else "",
        adviser_comment=comment,
    )


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "ADVISER")
    st.title("Temporary Term Cards")
    st.caption(
        "The end-of-term slip for learners (§39) — only the subjects active in the "
        f"selected term, with that term's average. {CARDS_PER_PAGE} cards per "
        "8.5 × 13 in sheet, paginated automatically."
    )
    render_flashes()

    adviser_scoped = not current_user.has_role("SUPER_ADMIN", "REGISTRAR")

    with get_session() as session:
        school = session.query(School).one_or_none()
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        if not school_years:
            st.warning("No school years yet.")
            return
        sy_by_id = {sy.id: sy for sy in school_years}
        sy_choice = st.selectbox(
            "School year", options=[sy.id for sy in school_years], format_func=lambda v: sy_by_id[v].name
        )

        sections_query = session.query(Section).filter_by(school_year_id=sy_choice)
        if adviser_scoped:
            sections_query = sections_query.filter_by(adviser_user_id=current_user.id)
        sections = sections_query.order_by(Section.name).all()
        if not sections:
            st.warning(
                "You're not the adviser of any section for this school year yet."
                if adviser_scoped
                else "No sections for this school year yet."
            )
            return
        section_by_id = {s.id: s for s in sections}
        section_choice = st.selectbox(
            "Section", options=[s.id for s in sections], format_func=lambda v: section_by_id[v].name
        )
        section = section_by_id[section_choice]

        terms = (
            session.query(Term).filter_by(school_year_id=sy_choice).order_by(Term.term_number).all()
        )
        if not terms:
            st.warning("This school year has no terms yet.")
            return
        term_by_id = {t.id: t for t in terms}
        term_choice = st.selectbox(
            "Term", options=[t.id for t in terms], format_func=lambda v: term_by_id[v].name
        )
        term = term_by_id[term_choice]

        enrollments = (
            session.query(Enrollment)
            .filter_by(section_id=section_choice, school_year_id=sy_choice)
            .join(Learner, Learner.id == Enrollment.learner_id)
            .order_by(Learner.last_name, Learner.first_name)
            .all()
        )
        if not enrollments:
            st.info("No learners enrolled in this section yet.")
            return

        adviser = session.get(User, section.adviser_user_id) if section.adviser_user_id else None
        grade_level = (
            session.get(GradeLevel, enrollments[0].grade_level_id)
            if enrollments[0].grade_level_id
            else None
        )
        # Everything the roster needs, in a fixed number of queries rather
        # than a few per learner (the database is ~85ms away).
        context = load_report_context(session, enrollments)
        learners = {
            l.id: l
            for l in session.query(Learner)
            .filter(Learner.id.in_([e.learner_id for e in enrollments]))
            .all()
        }
        learner_by_enrollment = {e.id: learners.get(e.learner_id) for e in enrollments}
        summaries = {
            s.enrollment_id: s
            for s in session.query(TermGradeSummary)
            .filter(
                TermGradeSummary.enrollment_id.in_([e.id for e in enrollments]),
                TermGradeSummary.term_id == term.id,
            )
            .all()
        }

        def card(enrollment):
            return _card_for(
                session, enrollment, learner_by_enrollment[enrollment.id],
                school=school, term=term, grade_level=grade_level,
                section=section, adviser=adviser,
                context=context, summary=summaries.get(enrollment.id),
            )

        st.divider()
        st.subheader(f"{term.name} — {section.name}")

        preview_rows = []
        for enrollment in enrollments:
            summary = summaries.get(enrollment.id)
            learner = learner_by_enrollment[enrollment.id]
            active = build_term_subject_rows(session, enrollment, term.term_number, context)
            preview_rows.append(
                {
                    "Learner": f"{learner.last_name}, {learner.first_name}",
                    "Active subjects": len(active),
                    "Encoded": sum(1 for _, g in active if g is not None),
                    "Term Average": _fmt(summary.term_average if summary else None),
                    "Completion": summary.completion_status.value if summary else "not computed",
                }
            )
        st.table(preview_rows)
        st.caption(
            "The Grade 11 language pair is listed as two separate subjects — §17 "
            "computes the Term Average that way, so the card's list matches the "
            "figure printed beneath it."
        )

        st.divider()
        st.subheader("Print")
        stem = f"TermCards_{section.name.replace(' ', '')}_{term.name.replace(' ', '')}"

        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                f"Print section — {len(enrollments)} card(s), "
                f"{page_count(len(enrollments))} sheet(s)",
                data=generate_term_cards([card(e) for e in enrollments]),
                file_name=f"{stem}.pdf",
                mime="application/pdf",
                type="primary",
            )
        with col2:
            chosen = st.selectbox(
                "Single learner",
                options=[e.id for e in enrollments],
                format_func=lambda v: (
                    f"{learner_by_enrollment[v].last_name}, {learner_by_enrollment[v].first_name}"
                ),
                key="term_card_learner",
            )
            enrollment = next(e for e in enrollments if e.id == chosen)
            learner = learner_by_enrollment[chosen]
            st.download_button(
                "Print selected learner",
                data=generate_term_cards([card(enrollment)]),
                file_name=f"TermCard_{learner.last_name}_{learner.first_name}.pdf".replace(" ", ""),
                mime="application/pdf",
            )
