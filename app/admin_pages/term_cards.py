import streamlit as st

from app.admin_pages._helpers import get_session, render_flashes, section_picker
from app.auth import require_role
from app.models.academic_structure import GradeLevel
from app.models.grades import TermGradeSummary
from app.models.learners import Enrollment, Learner
from app.models.organization import School, SchoolYear, Term
from app.models.rbac import User
from app.report_card import build_term_subject_rows, load_report_context
from app.roster_order import learner_order_by
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
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "ADVISER", "SCHOOL_HEAD")
    st.title("Temporary Term Cards")
    st.caption(
        "The end-of-term slip for learners — only the subjects active in the "
        f"selected term, with that term's average. {CARDS_PER_PAGE} cards per "
        "8.5 × 13 in sheet, paginated automatically."
    )
    render_flashes()

    adviser_scoped = not current_user.has_role("SUPER_ADMIN", "REGISTRAR", "SCHOOL_HEAD")

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

        section = section_picker(
            session, sy_choice, key="term_cards",
            adviser_user_id=current_user.id if adviser_scoped else None,
        )
        if section is None:
            return
        section_choice = section.id

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
            .order_by(*learner_order_by(Learner))
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
            "The Grade 11 language pair is printed as one combined learning area "
            "(a parent row plus its two indented components), so \"Active subjects\" "
            "counts one row more than the section's real subject count — the card's "
            "list still adds up to the Term Average printed beneath it."
        )

        st.divider()
        st.subheader("Print")
        stem = f"TermCards_{section.name.replace(' ', '')}_{term.name.replace(' ', '')}"

        col1, col2 = st.columns(2)
        with col1:
            # Built on click, not on render. `st.download_button(data=...)`
            # evaluates its data every script run, and Streamlit re-runs on
            # every interaction — so leaving this ungated rendered the whole
            # section's cards each time the term or section dropdown moved,
            # for a download nobody had asked for. Same reason SF9's batch
            # print sits behind a button.
            st.caption(
                f"{len(enrollments)} card(s), {page_count(len(enrollments))} sheet(s)."
            )
            if st.button("Build section PDF", type="primary"):
                data = generate_term_cards([card(e) for e in enrollments])
                st.success(f"Ready — {len(data) / 1024:,.0f} KB.")
                st.download_button(
                    "Download section PDF",
                    data=data,
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
