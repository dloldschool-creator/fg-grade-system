import streamlit as st

from app.admin_pages._helpers import (
    clear_text_fields,
    get_session,
    render_flashes,
    text_field,
    try_commit,
)
from app.auth import require_role
from app.models.awards import AwardPolicy, AwardPolicyVersion
from app.models.enums import AwardScope, CertificateLayout, PolicyVersionStatus
from app.models.organization import SchoolYear

_GUIDE_MARKDOWN = """
**Important:** Don't edit a version that has already been used to give out awards — create a new version instead.

Versions aren't editable in place. So, the actual flow to change a threshold (e.g. raising the honors cutoff for next year) is: leave the old version alone, open "Create new version," pick the new/next school year, re-enter all the same settings with your updated numbers, and submit. The old version stays as a historical record of what was used to judge that year's awards.

On the Awards page itself, you'll then pick the school year and the specific version (by policy name + version number) from a dropdown to run eligibility for a section.

**Award Policy** = just the named award itself — e.g. "Academic Excellence" or "Legacy Tiered Honors". A policy row only has a name and description. It holds no rules on its own.

**Award Policy Version** = the actual rules for that award — thresholds, what average it's judged against, requirements — scoped to one school year. Each policy can have several versions over time (v1, v2, v3...), and the version is what the Awards page actually uses when computing eligibility.

**"Add award policy"**

Creates a brand-new named award type. Use this for adding a genuinely new award beyond the two that already exist (Academic Excellence, Legacy Tiered Honors) — just a name + optional description, no rules yet. After adding one, you immediately need to create a version for it (see below) or it can't be used.

**"Create new version for [Policy]"**

This is where you configure the rules, per school year:

* **Effective school year** — which SY this version applies to. The Awards page only offers versions whose school year matches the year you're working in.
* **Judged against (scope)** — TERM (judged against each term's Term Average, awardable up to 3×/year — the Legacy Honors shape) or ANNUAL (judged once, against the year's General Average — the Academic Excellence shape).
* **Require complete record / no derogatory record / no failed subject** — checkboxes for eligibility.
* **Single-tier thresholds** — a flat min average and/or min lowest single grade. Leave both at 0 to skip.
* **Tiered thresholds** — up to 3 named tiers (e.g. "With Honors", "With High Honors", "With Highest Honors"), each with its own minimum. Filling these in overrides the single-tier fields above.
* **Certificate layout** — one certificate per page (an official issuance) or two per page (saves paper for classroom-level recognition). The Awards page's batch print picks this up automatically.
* **Custom certificate body** — optional; replaces the default certificate wording with your own, with variables filled in for you. Leave blank to keep the standard wording.
* **Signatory overrides** — optional, up to 3, in addition to the class adviser (who always signs and is never one of these 3). Leave blank to keep the single default signatory from School Info.
* **Status** — DRAFT / ACTIVE / ARCHIVED. This is just a label for bookkeeping — the Awards page's version picker lists every version effective for the chosen school year regardless of status, so an ACTIVE and a DRAFT version for the same year would both show up as selectable options. Don't rely on status alone to hide a half-configured version — use the school year field to keep it out of the picker until you're ready.
"""


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
    with st.expander("Award Policy Guide"):
        st.markdown(_GUIDE_MARKDOWN)
    render_flashes()

    with get_session() as session:
        policies = session.query(AwardPolicy).order_by(AwardPolicy.name).all()
        school_years = session.query(SchoolYear).order_by(SchoolYear.name.desc()).all()
        sy_by_id = {sy.id: sy for sy in school_years}

        # Grouped above the loop rather than queried per policy — see the
        # expander note in CLAUDE.md; the list is short today but the rule
        # is the same one every per-row panel follows.
        versions_by_policy: dict = {}
        for version in (
            session.query(AwardPolicyVersion)
            .order_by(AwardPolicyVersion.version_number.desc())
            .all()
        ):
            versions_by_policy.setdefault(version.award_policy_id, []).append(version)

        for policy in policies:
            st.subheader(policy.name)
            if policy.description:
                st.caption(policy.description)
            versions = versions_by_policy.get(policy.id, [])
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
                extras = []
                if v.certificate_layout == CertificateLayout.TWO_PER_PAGE:
                    extras.append("2 certificates/page")
                if v.certificate_body_template:
                    extras.append("custom certificate body")
                if v.signatory_overrides:
                    extras.append(f"{len(v.signatory_overrides)} signatory override(s)")
                extras_suffix = f" — {', '.join(extras)}" if extras else ""
                st.write(
                    f"**v{v.version_number}** ({v.status.value}) — **{scope_label}** — "
                    f"{'complete record required, ' if v.require_complete_record else ''}"
                    f"{'no derogatory record, ' if v.require_no_derogatory_record else ''}"
                    f"{'no failed subject, ' if v.require_no_failed_subject else ''}"
                    f"{shape} — effective {sy_by_id.get(v.effective_school_year_id).name if v.effective_school_year_id else '—'}"
                    f"{extras_suffix}"
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

                    certificate_layout = st.radio(
                        "Certificate layout",
                        options=[CertificateLayout.ONE_PER_PAGE.value, CertificateLayout.TWO_PER_PAGE.value],
                        format_func=lambda v: (
                            "One certificate per page (official issuance)"
                            if v == CertificateLayout.ONE_PER_PAGE.value
                            else "Two certificates per page (classroom recognition, saves paper)"
                        ),
                        index=0,
                        key=f"layout_{policy.id}",
                    )

                    st.markdown(
                        "**Custom certificate body** (optional — leave blank to keep the standard "
                        "wording). Placeholders filled in for you: `{learner_name}`, `{award_name}`, "
                        "`{average}`, `{average_label}`, `{date}`, `{school_year}`, `{venue}`, "
                        "`{school_name}`."
                    )
                    certificate_body_template = st.text_area(
                        "Custom body text",
                        key=f"body_{policy.id}",
                        placeholder=(
                            "for earning {award_name} with a {average_label} of {average}.\n"
                            "Given this {date} at {venue}, during School Year {school_year}."
                        ),
                    )

                    st.markdown(
                        "**Signatory overrides** (optional, up to 3 — in addition to the class "
                        "adviser, who always signs and is never one of these 3). Leave a row's name "
                        "blank to omit it; if none are filled in, the certificate falls back to the "
                        "single signatory set on the Awards page."
                    )
                    signatory_overrides = []
                    for i in range(3):
                        scol1, scol2 = st.columns(2)
                        sig_name = scol1.text_input(f"Signatory {i + 1} name", key=f"signame_{policy.id}_{i}")
                        sig_position = scol2.text_input(
                            f"Signatory {i + 1} position", key=f"sigpos_{policy.id}_{i}"
                        )
                        if sig_name:
                            signatory_overrides.append({"name": sig_name, "position": sig_position})

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
                                certificate_layout=CertificateLayout(certificate_layout),
                                certificate_body_template=certificate_body_template or None,
                                signatory_overrides=signatory_overrides or None,
                                status=PolicyVersionStatus(status),
                            )
                        )
                        try_commit(session, f"Created version {next_version} for {policy.name}.")
                        st.rerun()
            st.divider()

        st.subheader("Add award policy")
        with st.form("add_award_policy"):
            name = text_field("Name", key="add_award_policy.name")
            description = text_field(
                "Description", key="add_award_policy.description", area=True
            )
            if st.form_submit_button("Add"):
                if not name:
                    st.error("Name is required.")
                else:
                    session.add(AwardPolicy(name=name, description=description or None))
                    if try_commit(session, f"Added {name}."):
                        clear_text_fields("add_award_policy")
                    st.rerun()
