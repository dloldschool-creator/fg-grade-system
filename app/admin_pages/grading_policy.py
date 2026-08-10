import streamlit as st

from app.admin_pages._helpers import flash, get_session, render_flashes, try_commit
from app.auth import require_role
from app.models.enums import PolicyVersionStatus
from app.models.organization import SchoolYear
from app.models.subjects import GradingPolicy, GradingPolicyVersion


def render() -> None:
    require_role("SUPER_ADMIN")
    st.title("Grading Policy")
    st.caption(
        "Versioned so a passing-grade change never mutates a finalized grade's basis "
        "(§59) — never edit a version already used by finalized grades; create a new one."
    )
    render_flashes()

    with get_session() as session:
        policies = session.query(GradingPolicy).order_by(GradingPolicy.name).all()
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        sy_by_id = {sy.id: sy for sy in school_years}

        for policy in policies:
            st.subheader(policy.name)
            versions = (
                session.query(GradingPolicyVersion)
                .filter_by(grading_policy_id=policy.id)
                .order_by(GradingPolicyVersion.version_number.desc())
                .all()
            )
            st.table(
                [
                    {
                        "Version": v.version_number,
                        "Passing grade": float(v.passing_grade),
                        "Min/Max": f"{float(v.min_grade)}-{float(v.max_grade)}",
                        "Status": v.status.value,
                        "Effective SY": sy_by_id[v.effective_school_year_id].name
                        if v.effective_school_year_id
                        else "—",
                    }
                    for v in versions
                ]
            )
            with st.form(f"add_version_{policy.id}"):
                st.caption(f"New version for {policy.name}")
                next_version = (versions[0].version_number + 1) if versions else 1
                st.write(f"Version number: {next_version}")
                passing_grade = st.number_input(
                    "Passing grade", min_value=0.0, max_value=100.0, value=75.0,
                    key=f"pg_{policy.id}",
                )
                min_grade = st.number_input(
                    "Min grade", min_value=0.0, max_value=100.0, value=60.0,
                    key=f"min_{policy.id}",
                )
                max_grade = st.number_input(
                    "Max grade", min_value=0.0, max_value=100.0, value=100.0,
                    key=f"max_{policy.id}",
                )
                sy_choice = st.selectbox(
                    "Effective school year",
                    options=[sy.id for sy in school_years],
                    format_func=lambda v: sy_by_id[v].name,
                    key=f"sy_{policy.id}",
                )
                status = st.selectbox(
                    "Status",
                    options=[s.value for s in PolicyVersionStatus],
                    key=f"status_{policy.id}",
                )
                if st.form_submit_button("Create version"):
                    session.add(
                        GradingPolicyVersion(
                            grading_policy_id=policy.id,
                            version_number=next_version,
                            effective_school_year_id=sy_choice,
                            passing_grade=passing_grade,
                            min_grade=min_grade,
                            max_grade=max_grade,
                            status=PolicyVersionStatus(status),
                        )
                    )
                    try_commit(session, f"Created version {next_version}.")
                    st.rerun()
            st.divider()

        st.subheader("Add grading policy")
        with st.form("add_policy"):
            name = st.text_input("Name")
            description = st.text_area("Description", value="")
            if st.form_submit_button("Add"):
                if not name:
                    st.error("Name is required.")
                else:
                    session.add(GradingPolicy(name=name, description=description or None))
                    session.commit()
                    flash("success", f"Added {name}.")
                    st.rerun()
