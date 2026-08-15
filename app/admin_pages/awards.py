from datetime import date

import streamlit as st

from app.admin_pages._helpers import flash, get_session, render_flashes, section_picker
from app.auth import require_role
from app.award_service import clear_award_override, compute_award_eligibility, set_award_override
from app.certificate_generator import (
    CertificateData,
    certificate_award_name,
    generate_award_certificate,
    generate_award_certificates_2up,
)
from app.models.awards import AwardPolicy, AwardPolicyVersion, LearnerAward
from app.models.enums import AwardResult, AwardScope
from app.models.grades import AnnualGradeSummary, TermGradeSummary
from app.models.learners import Enrollment, Learner
from app.models.organization import School, SchoolYear, Term
from app.models.rbac import User
from app.roster_order import learner_order_by


def _load_award_context(session, enrollments, version, version_choice, term_choice) -> dict:
    """Everything the roster needs, in three queries instead of four per
    learner.

    Both loops below used to resolve the learner, their award row and
    their grade summary one at a time. At ~85ms a round trip that was
    about 160 queries — thirteen seconds — for a forty-learner section,
    paid again on every widget interaction because Streamlit re-runs the
    whole script each time.
    """
    ids = [e.id for e in enrollments]
    learner_ids = [e.learner_id for e in enrollments]

    learners = {
        learner.id: learner
        for learner in session.query(Learner).filter(Learner.id.in_(learner_ids)).all()
    }
    awards = {
        award.enrollment_id: award
        for award in session.query(LearnerAward)
        .filter(
            LearnerAward.enrollment_id.in_(ids),
            LearnerAward.award_policy_version_id == version_choice,
            LearnerAward.term_id == term_choice,
        )
        .all()
    }
    if version.scope == AwardScope.TERM:
        summaries = {
            row.enrollment_id: row.term_average
            for row in session.query(TermGradeSummary)
            .filter(
                TermGradeSummary.enrollment_id.in_(ids),
                TermGradeSummary.term_id == term_choice,
            )
            .all()
        }
    else:
        summaries = {
            row.enrollment_id: row.general_average
            for row in session.query(AnnualGradeSummary)
            .filter(AnnualGradeSummary.enrollment_id.in_(ids))
            .all()
        }
    return {"learners": learners, "awards": awards, "averages": summaries}


def _certificate_data(
    school, school_year, learner, award, average, term_name,
    adviser, signatory, position, issued_on, venue,
) -> CertificateData:
    return CertificateData(
        school_name=school.school_name,
        schools_division=school.schools_division,
        learner_name=f"{learner.last_name}, {learner.first_name}",
        # The policy's administrative name carries the DepEd order and a
        # version; neither belongs on a learner's certificate.
        award_name=certificate_award_name(award.award_name),
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
    enrollments, context, term_name,
    school, school_year, adviser, signatory, position, issued_on, venue,
) -> None:
    """One PDF for every eligible learner in the section, two to a page.

    Reads the preloaded context rather than querying per learner, and
    renders nothing until asked: `st.download_button(data=...)` evaluates
    its data on every script run, so an ungated one rebuilt the whole
    section's certificates each time any widget on this page moved.
    """
    eligible = []
    for enrollment in enrollments:
        award = context["awards"].get(enrollment.id)
        if award is None or award.award_result != AwardResult.ELIGIBLE_AWARDED:
            continue
        learner = context["learners"].get(enrollment.learner_id)
        if learner is None:
            continue
        eligible.append(
            _certificate_data(
                school, school_year, learner, award,
                context["averages"].get(enrollment.id), term_name,
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
    st.caption(
        f"{len(eligible)} eligible learner(s) — {pages} sheet(s), two half-page "
        "certificates per sheet with a cut line between them."
    )
    if st.button(f"Build {len(eligible)} certificate(s)", type="primary"):
        data = generate_award_certificates_2up(eligible)
        st.success(f"Ready — {len(data) / 1024:,.0f} KB.")
        st.download_button(
            "Download certificates PDF",
            data=data,
            file_name=(
                f"certificates_{school_year.name}"
                f"{'_' + term_name.replace(' ', '') if term_name else ''}.pdf"
            ),
            mime="application/pdf",
            type="primary",
        )


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
            "The signatory and date come from School Info and the school year. "
            "Change them here if these certificates need someone else to sign — a "
            "term recognition signed by your immediate supervisor, for example. "
            "Nothing is saved; it only affects the certificates you make right now."
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
        "Works from the averages already shown on Grade Summary — it never changes "
        "a grade. A reason is always recorded, including when a learner is not "
        "eligible."
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

        section = section_picker(
            session, sy_choice, key="awards", adviser_user_id=adviser_user_id
        )
        if section is None:
            return
        section_choice = section.id
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
                "Judged on this term's **Term Average**. For Grade 11, Effective "
                "Communication and Mabisang Komunikasyon count as two separate "
                "subjects here."
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
            .order_by(*learner_order_by(Learner))
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

        # Three queries for the whole roster, read by both the batch print
        # and the per-learner panels below.
        context = _load_award_context(
            session, enrollments, version, version_choice, term_choice
        )

        _batch_download(
            enrollments, context, term_name,
            school, school_year, adviser, signatory, position, issued_on, venue,
        )
        st.divider()

        for enrollment in enrollments:
            learner = context["learners"].get(enrollment.learner_id)
            award = context["awards"].get(enrollment.id)
            average = context["averages"].get(enrollment.id)
            if learner is None:
                continue

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
                    st.caption("Manual override — a reason is required, and the change is recorded.")
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
                    # Built on click. Streamlit runs an expander's body
                    # whether or not it is open, so generating here
                    # unconditionally rendered a certificate for every
                    # eligible learner in the section on every rerun.
                    if st.button("Build certificate", key=f"build_{award.id}"):
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
