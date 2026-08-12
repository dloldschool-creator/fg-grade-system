import streamlit as st

from app.admin_pages._helpers import get_session, render_flashes, try_commit
from app.auth import require_role
from app.models.awards import AwardPolicy, AwardPolicyVersion
from app.models.enums import AwardScope, PolicyVersionStatus
from app.models.organization import SchoolYear


def _tier_editor(key_prefix: str, existing: list[dict] | None = None) -> list[dict]:
    """Fixed 3-tier editor (covers the Legacy Honors shape) — Streamlit
    forms can't grow/shrink widget counts dynamically, so this isn't a
    general N-tier builder. Leave a row's label blank to omit that tier."""
    existing = existing or []
    tiers = []
    for i in range(3):
        default_label = existing[i]["label"] if i < len(existing) else ""
        # The stored key stays `min_general_average` for every scope —
        # historical, and kept so already-seeded JSONB stays readable.
        # Under a TERM-scoped policy it means "minimum Term Average".
        default_min = existing[i]["min_general_average"] if i < len(existing) else 0
        col1, col2 = st.columns(2)
        label = col1.text_input(f"Tier {i + 1} label", value=default_label, key=f"{key_prefix}_label_{i}")
        min_ga = col2.number_input(
            f"Tier {i + 1} minimum average",
            min_value=0.0,
            max_value=100.0,
            value=float(default_min),
            key=f"{key_prefix}_min_{i}",
        )
        if label:
            tiers.append({"label": label, "min_general_average": min_ga})
    return tiers


def render() -> None:
    require_role("SUPER_ADMIN")
    st.title("Award Policy")
    st.caption(
        "Two separate award policies. Don't edit a version that has already been "
        "used to give out awards — create a new version instead."
    )
    render_flashes()

    with get_session() as session:
        policies = session.query(AwardPolicy).order_by(AwardPolicy.name).all()
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        sy_by_id = {sy.id: sy for sy in school_years}

        for policy in policies:
            st.subheader(policy.name)
            if policy.description:
                st.caption(policy.description)
            versions = (
                session.query(AwardPolicyVersion)
                .filter_by(award_policy_id=policy.id)
                .order_by(AwardPolicyVersion.version_number.desc())
                .all()
            )
            for v in versions:
                scope_label = (
                    "per term, on the Term Average"
                    if v.scope == AwardScope.TERM
                    else "annual, on the General Average"
                )
                average_word = "TA" if v.scope == AwardScope.TERM else "GA"
                if v.tier_thresholds:
                    shape = ", ".join(
                        f"{t['label']} ({average_word}≥{t['min_general_average']})"
                        for t in v.tier_thresholds
                    )
                else:
                    parts = []
                    if v.min_general_average is not None:
                        parts.append(f"{average_word}≥{float(v.min_general_average)}")
                    if v.min_lowest_final_grade is not None:
                        parts.append(f"lowest grade≥{float(v.min_lowest_final_grade)}")
                    shape = ", ".join(parts) or "no thresholds set"
                st.write(
                    f"**v{v.version_number}** ({v.status.value}) — **{scope_label}** — "
                    f"{'complete record required, ' if v.require_complete_record else ''}"
                    f"{'no derogatory record, ' if v.require_no_derogatory_record else ''}"
                    f"{'no failed subject, ' if v.require_no_failed_subject else ''}"
                    f"{shape} — effective {sy_by_id.get(v.effective_school_year_id).name if v.effective_school_year_id else '—'}"
                )

            with st.expander(f"Create new version for {policy.name}"):
                with st.form(f"add_version_{policy.id}"):
                    next_version = (versions[0].version_number + 1) if versions else 1
                    st.write(f"Version number: {next_version}")
                    sy_choice = st.selectbox(
                        "Effective school year",
                        options=[sy.id for sy in school_years],
                        format_func=lambda v: sy_by_id[v].name,
                        key=f"sy_{policy.id}",
                    )
                    scope = st.radio(
                        "Judged against",
                        options=[AwardScope.TERM.value, AwardScope.ANNUAL.value],
                        format_func=lambda s: (
                            "Each term's Term Average — awarded up to 3× a year"
                            if s == AwardScope.TERM.value
                            else "The annual General Average — awarded once a year"
                        ),
                        index=1,
                        key=f"scope_{policy.id}",
                    )
                    require_complete_record = st.checkbox(
                        "Require complete record", value=True, key=f"reqc_{policy.id}"
                    )
                    require_no_derogatory_record = st.checkbox(
                        "Require no derogatory record", value=True, key=f"reqd_{policy.id}"
                    )
                    require_no_failed_subject = st.checkbox(
                        "Require no failed subject", value=False, key=f"reqf_{policy.id}"
                    )

                    st.markdown(
                        "**Single-tier thresholds** (leave at 0 to skip — used by policies "
                        "like Academic Excellence). Both are read against whichever average "
                        "the scope above selects."
                    )
                    col1, col2 = st.columns(2)
                    min_general_average = col1.number_input(
                        "Min average", min_value=0.0, max_value=100.0, value=0.0, key=f"minga_{policy.id}"
                    )
                    min_lowest_final_grade = col2.number_input(
                        "Min lowest single grade", min_value=0.0, max_value=100.0, value=0.0, key=f"minlow_{policy.id}"
                    )

                    st.markdown("**Tiered thresholds** (fill in to make this a tiered policy like Legacy Honors — overrides the single-tier fields above)")
                    tiers = _tier_editor(f"tier_{policy.id}")

                    status = st.selectbox(
                        "Status", options=[s.value for s in PolicyVersionStatus], key=f"status_{policy.id}"
                    )

                    if st.form_submit_button("Create version"):
                        session.add(
                            AwardPolicyVersion(
                                award_policy_id=policy.id,
                                version_number=next_version,
                                effective_school_year_id=sy_choice,
                                scope=AwardScope(scope),
                                require_complete_record=require_complete_record,
                                require_no_derogatory_record=require_no_derogatory_record,
                                require_no_failed_subject=require_no_failed_subject,
                                min_general_average=min_general_average or None,
                                min_lowest_final_grade=min_lowest_final_grade or None,
                                tier_thresholds=tiers or None,
                                status=PolicyVersionStatus(status),
                            )
                        )
                        try_commit(session, f"Created version {next_version} for {policy.name}.")
                        st.rerun()
            st.divider()

        st.subheader("Add award policy")
        with st.form("add_award_policy"):
            name = st.text_input("Name")
            description = st.text_area("Description", value="")
            if st.form_submit_button("Add"):
                if not name:
                    st.error("Name is required.")
                else:
                    session.add(AwardPolicy(name=name, description=description or None))
                    try_commit(session, f"Added {name}.")
                    st.rerun()
