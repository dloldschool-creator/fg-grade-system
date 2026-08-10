import streamlit as st

from app.admin_pages._helpers import flash, get_session, render_flashes
from app.auth import require_role
from app.award_service import clear_award_override, compute_award_eligibility, set_award_override
from app.certificate_generator import generate_award_certificate
from app.models.academic_structure import Section
from app.models.awards import AwardPolicy, AwardPolicyVersion, LearnerAward
from app.models.enums import AwardResult, AwardScope
from app.models.grades import AnnualGradeSummary, TermGradeSummary
from app.models.learners import Enrollment, Learner
from app.models.organization import School, SchoolYear, Term
from app.models.rbac import User


def render() -> None:
    current_user = require_role("SUPER_ADMIN", "REGISTRAR", "ADVISER")
    st.title("Awards")
    st.caption(
        "Computes eligibility from the already-computed General Average / completion "
        "status on Grade Summary — never recomputes grades itself. Always records a "
        "reason, even when not eligible (§24)."
    )
    render_flashes()

    adviser_user_id = None if current_user.has_role("SUPER_ADMIN", "REGISTRAR") else current_user.id

    with get_session() as session:
        school = session.query(School).one_or_none()
        if school is None:
            st.warning("Set up School Info first.")
            return

        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        if not school_years:
            st.warning("No school years yet.")
            return
        sy_by_id = {sy.id: sy for sy in school_years}
        sy_choice = st.selectbox(
            "School year", options=[sy.id for sy in school_years], format_func=lambda v: sy_by_id[v].name
        )
        school_year = sy_by_id[sy_choice]

        section_query = session.query(Section).filter_by(school_year_id=sy_choice)
        if adviser_user_id is not None:
            section_query = section_query.filter_by(adviser_user_id=adviser_user_id)
        sections = section_query.order_by(Section.name).all()
        if not sections:
            st.warning(
                "You're not the adviser of any section for this school year yet."
                if adviser_user_id
                else "No sections for this school year yet."
            )
            return
        section_by_id = {s.id: s for s in sections}
        section_choice = st.selectbox(
            "Section", options=[s.id for s in sections], format_func=lambda v: section_by_id[v].name
        )
        section = section_by_id[section_choice]
        adviser = session.get(User, section.adviser_user_id) if section.adviser_user_id else None

        policy_versions = (
            session.query(AwardPolicyVersion)
            .filter_by(effective_school_year_id=sy_choice)
            .all()
        )
        if not policy_versions:
            st.warning("No award policy versions effective for this school year yet — set one up on the Award Policy page.")
            return
        policy_by_id = {p.id: p for p in session.query(AwardPolicy).all()}
        version_by_id = {v.id: v for v in policy_versions}
        version_choice = st.selectbox(
            "Award policy",
            options=[v.id for v in policy_versions],
            format_func=lambda v: f"{policy_by_id[version_by_id[v].award_policy_id].name} (v{version_by_id[v].version_number})",
        )
        version = version_by_id[version_choice]

        # A TERM-scoped policy is judged once per term against that term's
        # Term Average (§17), so the term is part of the selection; an
        # ANNUAL one has no term dimension at all.
        term_choice = None
        term_name = None
        if version.scope == AwardScope.TERM:
            terms = (
                session.query(Term)
                .filter_by(school_year_id=sy_choice)
                .order_by(Term.term_number)
                .all()
            )
            if not terms:
                st.warning("This school year has no terms yet.")
                return
            term_by_id = {t.id: t for t in terms}
            term_choice = st.selectbox(
                "Term", options=[t.id for t in terms], format_func=lambda v: term_by_id[v].name
            )
            term_name = term_by_id[term_choice].name
            st.caption(
                "Judged on this term's **Term Average**, which counts the Grade 11 "
                "language pair as two separate subjects (§17) — unlike the General "
                "Average, where they combine into one."
            )
        else:
            st.caption("Judged once for the year on the **General Average** across all terms.")

        enrollments = (
            session.query(Enrollment)
            .filter_by(section_id=section.id, school_year_id=sy_choice)
            .join(Learner, Learner.id == Enrollment.learner_id)
            .order_by(Learner.last_name, Learner.first_name)
            .all()
        )
        if not enrollments:
            st.info("No learners enrolled in this section yet.")
            return

        if st.button("Compute eligibility for all"):
            for enrollment in enrollments:
                compute_award_eligibility(session, enrollment.id, version_choice, term_choice)
            flash("success", f"Computed eligibility for {len(enrollments)} learner(s).")
            st.rerun()

        for enrollment in enrollments:
            learner = session.get(Learner, enrollment.learner_id)
            award = (
                session.query(LearnerAward)
                .filter_by(
                    enrollment_id=enrollment.id,
                    award_policy_version_id=version_choice,
                    term_id=term_choice,
                )
                .one_or_none()
            )
            if version.scope == AwardScope.TERM:
                summary = (
                    session.query(TermGradeSummary)
                    .filter_by(enrollment_id=enrollment.id, term_id=term_choice)
                    .one_or_none()
                )
                average = summary.term_average if summary else None
            else:
                summary = (
                    session.query(AnnualGradeSummary)
                    .filter_by(enrollment_id=enrollment.id)
                    .one_or_none()
                )
                average = summary.general_average if summary else None

            status_label = award.award_result.value if award else "not computed yet"
            with st.expander(f"{learner.last_name}, {learner.first_name} — {status_label}"):
                if award is None:
                    st.caption("Not computed yet — click 'Compute eligibility for all' above.")
                    continue

                st.write(f"**Result:** {award.award_result.value}")
                if award.award_name:
                    st.write(f"**Award:** {award.award_name}")
                average_label = "Term Average" if version.scope == AwardScope.TERM else "General Average"
                st.write(f"**{average_label}:** {int(average) if average is not None else '—'}")
                st.write(f"**Reason:** {award.reason}")
                if award.is_override:
                    st.warning(f"Manually overridden by an admin: {award.override_reason}")
                    if st.button("Clear override", key=f"clear_override_{award.id}"):
                        clear_award_override(session, award)
                        flash("success", "Override cleared — will recompute on next run.")
                        st.rerun()

                with st.form(f"override_{award.id}"):
                    st.caption("Manual override — requires a reason, and is audited (§40, §67).")
                    override_result = st.selectbox(
                        "Override result", options=[r.value for r in AwardResult], key=f"or_{award.id}"
                    )
                    override_award_name = st.text_input("Award name (if eligible)", key=f"oan_{award.id}")
                    override_reason = st.text_area("Reason for override", key=f"orr_{award.id}")
                    if st.form_submit_button("Apply override"):
                        if not override_reason:
                            st.error("A reason is required.")
                        else:
                            set_award_override(
                                session,
                                award,
                                AwardResult(override_result),
                                override_award_name or None,
                                current_user.id,
                                override_reason,
                            )
                            flash("success", "Override applied.")
                            st.rerun()

                if award.award_result == AwardResult.ELIGIBLE_AWARDED and school_year.recognition_date is None:
                    st.info(
                        "Set a Recognition Date for this school year (School Years & Terms "
                        "page) before generating a certificate."
                    )
                elif award.award_result == AwardResult.ELIGIBLE_AWARDED:
                    pdf_bytes = generate_award_certificate(
                        school_name=school.school_name,
                        schools_division=school.schools_division,
                        learner_name=f"{learner.last_name}, {learner.first_name}",
                        award_name=award.award_name or "RECOGNITION",
                        general_average=average,
                        term_name=term_name,
                        recognition_date=school_year.recognition_date,
                        recognition_venue=school_year.recognition_venue or "",
                        school_year_name=school_year.name,
                        adviser_name=adviser.full_name if adviser else "",
                        school_head_name=school.school_head_name,
                        school_head_position=school.school_head_position,
                    )
                    st.download_button(
                        "Download certificate",
                        data=pdf_bytes,
                        file_name=(
                            f"certificate_{learner.last_name}_{learner.first_name}"
                            f"{'_' + term_name.replace(' ', '') if term_name else ''}.pdf"
                        ),
                        mime="application/pdf",
                        key=f"dl_{award.id}",
                    )
