from datetime import date

import streamlit as st

from app.admin_pages._helpers import flash, get_session, render_flashes
from app.auth import require_role
from app.award_service import clear_award_override, compute_award_eligibility, set_award_override
from app.certificate_generator import (
    CertificateData,
    generate_award_certificate,
    generate_award_certificates_2up,
)
from app.models.academic_structure import Section
from app.models.awards import AwardPolicy, AwardPolicyVersion, LearnerAward
from app.models.enums import AwardResult, AwardScope
from app.models.grades import AnnualGradeSummary, TermGradeSummary
from app.models.learners import Enrollment, Learner
from app.models.organization import School, SchoolYear, Term
from app.models.rbac import User


def _certificate_data(
    school, school_year, learner, award, average, term_name,
    adviser, signatory, position, issued_on, venue,
) -> CertificateData:
    return CertificateData(
        school_name=school.school_name,
        schools_division=school.schools_division,
        learner_name=f"{learner.last_name}, {learner.first_name}",
        award_name=award.award_name or "RECOGNITION",
        general_average=average,
        term_name=term_name,
        recognition_date=issued_on,
        recognition_venue=venue,
        school_year_name=school_year.name,
        adviser_name=adviser.full_name if adviser else "",
        school_head_name=signatory or "",
        school_head_position=position or "",
    )


def _batch_download(
    session, enrollments, version, version_choice, term_choice, term_name,
    school, school_year, adviser, signatory, position, issued_on, venue,
) -> None:
    """One PDF for every eligible learner in the section, two to a page."""
    eligible = []
    for enrollment in enrollments:
        award = (
            session.query(LearnerAward)
            .filter_by(
                enrollment_id=enrollment.id,
                award_policy_version_id=version_choice,
                term_id=term_choice,
            )
            .one_or_none()
        )
        if award is None or award.award_result != AwardResult.ELIGIBLE_AWARDED:
            continue
        learner = session.get(Learner, enrollment.learner_id)
        if version.scope == AwardScope.TERM:
            summary = (
                session.query(TermGradeSummary)
                .filter_by(enrollment_id=enrollment.id, term_id=term_choice)
                .one_or_none()
            )
            average = summary.term_average if summary else None
        else:
            summary = (
                session.query(AnnualGradeSummary).filter_by(enrollment_id=enrollment.id).one_or_none()
            )
            average = summary.general_average if summary else None
        eligible.append(
            _certificate_data(
                school, school_year, learner, award, average, term_name,
                adviser, signatory, position, issued_on, venue,
            )
        )

    if not eligible:
        st.caption("No eligible learners yet — nothing to print.")
        return
    if issued_on is None:
        st.info("Set a Recognition Date before printing certificates.")
        return

    pages = -(-len(eligible) // 2)  # ceil
    st.download_button(
        f"Print all {len(eligible)} certificate(s) — 2 per page ({pages} sheet(s))",
        data=generate_award_certificates_2up(eligible),
        file_name=(
            f"certificates_{school_year.name}"
            f"{'_' + term_name.replace(' ', '') if term_name else ''}.pdf"
        ),
        mime="application/pdf",
        type="primary",
    )
    st.caption("Two half-page certificates per sheet with a cut line between them.")


def _certificate_settings(version, school, school_year):
    """Who signs the certificate, and when it's dated.

    A TERM-scoped award (the tiered Honors) is **classroom-level
    recognition, not a DepEd order**, so the adviser can point the
    signature block at their immediate supervisor and set their own date
    rather than being tied to the school head and the school year's
    official recognition date.

    An ANNUAL award (Academic Excellence, DO 15 s.2026) *is* an official
    issuance, so it stays locked to the school head and the recognition
    date on the school year — those aren't the adviser's to change.
    """
    if version.scope != AwardScope.TERM:
        return (
            school.school_head_name,
            school.school_head_position,
            school_year.recognition_date,
            school_year.recognition_venue or "",
        )

    with st.expander("Certificate details — signatory and date"):
        st.caption(
            "Defaults come from School Info and the school year. Change them here "
            "for term recognition signed by your immediate supervisor; nothing is "
            "saved, it only affects the certificates you generate now."
        )
        col1, col2 = st.columns(2)
        signatory = col1.text_input(
            "Signatory name", value=school.school_head_name or "", key="cert_signatory"
        )
        position = col2.text_input(
            "Signatory position",
            value=school.school_head_position or "",
            key="cert_position",
        )
        col1, col2 = st.columns(2)
        issued_on = col1.date_input(
            "Date issued",
            value=school_year.recognition_date or date.today(),
            key="cert_date",
        )
        venue = col2.text_input(
            "Venue (optional)",
            value=school_year.recognition_venue or "",
            key="cert_venue",
        )
    return signatory, position, issued_on, venue


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

        signatory, position, issued_on, venue = _certificate_settings(
            version, school, school_year
        )

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

        _batch_download(
            session, enrollments, version, version_choice, term_choice, term_name,
            school, school_year, adviser, signatory, position, issued_on, venue,
        )
        st.divider()

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
                        clear_award_override(session, award, current_user.id)
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

                if award.award_result == AwardResult.ELIGIBLE_AWARDED and issued_on is None:
                    st.info(
                        "Set a Recognition Date for this school year (School Years & Terms "
                        "page) before generating a certificate."
                    )
                elif award.award_result == AwardResult.ELIGIBLE_AWARDED:
                    pdf_bytes = generate_award_certificate(
                        **_certificate_data(
                            school, school_year, learner, award, average, term_name,
                            adviser, signatory, position, issued_on, venue,
                        ).__dict__
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
