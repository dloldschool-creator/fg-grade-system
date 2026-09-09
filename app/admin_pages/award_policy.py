import streamlit as st
from sqlalchemy import func

from app import audit_service
from app.admin_pages._helpers import (
    clear_text_fields,
    get_session,
    render_flashes,
    text_field,
    try_commit,
    try_delete,
)
from app.auth import require_role
from app.models.awards import AwardPolicy, AwardPolicyVersion, LearnerAward
from app.models.enums import AwardScope, CertificateLayout, PolicyVersionStatus
from app.models.organization import SchoolYear

_GUIDE_MARKDOWN = """
**Important:** Don't edit a version that has already been used to give out awards — create a new version instead.

Versions aren't editable in place *once used*. So, the usual flow to change a threshold (e.g. raising the honors cutoff for next year) is: leave the old version alone, open "Create new version," pick the new/next school year, re-enter all the same settings with your updated numbers, and submit. The old version stays as a historical record of what was used to judge that year's awards.

A version that has **never** been used to compute or override a learner's award can still be edited or deleted in place — see "Edit"/"Delete" under a version below. Once even one learner award references it, editing and deleting both disappear; **Status** is the only thing you can still change (see below), and it never affects computation.

On the Awards page itself, you'll then pick the school year and the specific version (by policy name + version number) from a dropdown to run eligibility for a section.

**Award Policy** = just the named award itself — e.g. "Academic Excellence" or "Legacy Tiered Honors". A policy row only has a name and description. It holds no rules on its own. A policy with zero versions can be deleted outright — see "Delete policy" below.

**Award Policy Version** = the actual rules for that award — thresholds, what average it's judged against, requirements — scoped to one school year. Each policy can have several versions over time (v1, v2, v3...), and the version is what the Awards page actually uses when computing eligibility.

**"Add award policy"**

Creates a brand-new named award type. Use this for adding a genuinely new award beyond the ones that already exist — just a name + optional description, no rules yet. After adding one, you immediately need to create a version for it (see below) or it can't be used.

**"Create new version for [Policy]"**

This is where you configure the rules, per school year:

* **Effective school year** — which SY this version applies to. The Awards page only offers versions whose school year matches the year you're working in.
* **Judged against (scope)** — TERM (judged against each term's Term Average, awardable up to 3×/year — the Legacy Honors shape) or ANNUAL (judged once, against the year's General Average — the Academic Excellence shape).
* **Require complete record / no derogatory record / no failed subject / perfect attendance** — checkboxes for eligibility. These still apply even when Manual only (below) is checked. Perfect attendance means zero absences and zero tardies/cutting over this version's own period (the term below for TERM, the whole year for ANNUAL) — an unencoded day blocks it rather than being read as present. A pure attendance award (e.g. "Complete Attendance") just checks this one and leaves everything else at its default/blank — it's computed automatically, no manual override needed for the typical case.
* **Manual only** — for an award with no computable rule at all: Leadership, Best in Subject. Every learner defaults to Not Eligible no matter what the thresholds below say; grant it to specific learners with the override control on the Awards page. Overrides this checkbox — leave thresholds blank when it's on.
* **Single-tier thresholds** — a flat min average and/or min lowest single grade. Leave both at 0 to skip.
* **Tiered thresholds** — up to 3 named tiers (e.g. "With Honors", "With High Honors", "With Highest Honors"), each with its own minimum. Filling these in overrides the single-tier fields above.
* **Certificate layout** — one certificate per page (an official issuance) or two per page (saves paper for classroom-level recognition). The Awards page's batch print picks this up automatically.
* **Custom certificate body** — optional; replaces the default certificate wording with your own, with variables filled in for you. Leave blank to keep the standard wording.
* **Signatory overrides** — optional, up to 3, in addition to the class adviser (who always signs and is never one of these 3). Leave blank to keep the single default signatory from School Info.
* **Status** — DRAFT / ACTIVE / ARCHIVED. This is just a label for bookkeeping — the Awards page's version picker lists every version effective for the chosen school year regardless of status, so an ACTIVE and a DRAFT version for the same year would both show up as selectable options. Don't rely on status alone to hide a half-configured version — use the school year field to keep it out of the picker until you're ready. You can change it later at any time, whether or not the version has been used, from the "Status" control under the version.
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


def _version_snapshot(v: AwardPolicyVersion) -> dict:
    """The fields an edit can change, for the audit before/after pair.
    Excludes bookkeeping columns (id, status, created_*) — status has its
    own action and its own control, and the rest never change."""
    return {
        "effective_school_year_id": v.effective_school_year_id,
        "scope": v.scope,
        "require_complete_record": v.require_complete_record,
        "require_no_derogatory_record": v.require_no_derogatory_record,
        "require_no_failed_subject": v.require_no_failed_subject,
        "manual_only": v.manual_only,
        "require_perfect_attendance": v.require_perfect_attendance,
        "min_general_average": v.min_general_average,
        "min_lowest_final_grade": v.min_lowest_final_grade,
        "tier_thresholds": v.tier_thresholds,
        "certificate_layout": v.certificate_layout,
        "certificate_body_template": v.certificate_body_template,
        "signatory_overrides": v.signatory_overrides,
    }


def _render_version_fields(prefix: str, school_years, sy_by_id, existing: AwardPolicyVersion | None = None) -> dict:
    """Renders every configurable field of a version (prefilled from
    `existing` when editing), and returns the collected values. Doesn't
    render Status or a submit button — Create keeps its own Status
    picker, and a used-or-not version already has its own always-on
    Status control, so a status field inside this shared form would be a
    second, confusing way to do the same thing."""
    sy_options = [sy.id for sy in school_years]
    sy_default = existing.effective_school_year_id if existing else sy_options[0]
    sy_choice = st.selectbox(
        "Effective school year",
        options=sy_options,
        format_func=lambda v: sy_by_id[v].name,
        index=sy_options.index(sy_default) if sy_default in sy_options else 0,
        key=f"{prefix}_sy",
    )
    scope_options = [AwardScope.TERM.value, AwardScope.ANNUAL.value]
    scope_default = existing.scope.value if existing else AwardScope.ANNUAL.value
    scope = st.radio(
        "Judged against",
        options=scope_options,
        format_func=lambda s: (
            "Each term's Term Average — awarded up to 3× a year"
            if s == AwardScope.TERM.value
            else "The annual General Average — awarded once a year"
        ),
        index=scope_options.index(scope_default),
        key=f"{prefix}_scope",
    )
    require_complete_record = st.checkbox(
        "Require complete record",
        value=existing.require_complete_record if existing else True,
        key=f"{prefix}_reqc",
    )
    require_no_derogatory_record = st.checkbox(
        "Require no derogatory record",
        value=existing.require_no_derogatory_record if existing else True,
        key=f"{prefix}_reqd",
    )
    require_no_failed_subject = st.checkbox(
        "Require no failed subject",
        value=existing.require_no_failed_subject if existing else False,
        key=f"{prefix}_reqf",
    )
    require_perfect_attendance = st.checkbox(
        "Require perfect attendance",
        value=existing.require_perfect_attendance if existing else False,
        key=f"{prefix}_reqattend",
        help=(
            "Zero absences AND zero tardies/cutting over this version's own period "
            "(the term picked below for a TERM scope, the whole year for ANNUAL), "
            "with attendance fully encoded first — an unencoded day blocks it rather "
            "than counting as present. Combines with any other setting on this form; "
            "leave everything else blank/unchecked for a pure attendance award."
        ),
    )
    manual_only = st.checkbox(
        "Manual only — no automatic rule (Leadership, Best in Subject)",
        value=existing.manual_only if existing else False,
        key=f"{prefix}_manual",
        help=(
            "Every learner defaults to Not Eligible regardless of the thresholds "
            "below — grant this award to specific learners with the override "
            "control on the Awards page instead. The require-* checks above still "
            "apply (e.g. a derogatory record can still block it)."
        ),
    )

    st.markdown(
        "**Single-tier thresholds** (leave at 0 to skip — used by policies "
        "like Academic Excellence; ignored entirely when Manual only is checked "
        "above). Both are read against whichever average the scope above selects."
    )
    col1, col2 = st.columns(2)
    default_min_ga = float(existing.min_general_average) if existing and existing.min_general_average is not None else 0.0
    default_min_low = float(existing.min_lowest_final_grade) if existing and existing.min_lowest_final_grade is not None else 0.0
    min_general_average = col1.number_input(
        "Min average", min_value=0.0, max_value=100.0, value=default_min_ga, key=f"{prefix}_minga"
    )
    min_lowest_final_grade = col2.number_input(
        "Min lowest single grade", min_value=0.0, max_value=100.0, value=default_min_low, key=f"{prefix}_minlow"
    )

    st.markdown("**Tiered thresholds** (fill in to make this a tiered policy like Legacy Honors — overrides the single-tier fields above)")
    tiers = _tier_editor(f"{prefix}_tier", existing.tier_thresholds if existing else None)

    layout_options = [CertificateLayout.ONE_PER_PAGE.value, CertificateLayout.TWO_PER_PAGE.value]
    layout_default = existing.certificate_layout.value if existing else CertificateLayout.ONE_PER_PAGE.value
    certificate_layout = st.radio(
        "Certificate layout",
        options=layout_options,
        format_func=lambda v: (
            "One certificate per page (official issuance)"
            if v == CertificateLayout.ONE_PER_PAGE.value
            else "Two certificates per page (classroom recognition, saves paper)"
        ),
        index=layout_options.index(layout_default),
        key=f"{prefix}_layout",
    )

    st.markdown(
        "**Custom certificate body** (optional — leave blank to keep the standard "
        "wording). Placeholders filled in for you: `{learner_name}`, `{award_name}`, "
        "`{average}`, `{average_label}`, `{date}`, `{school_year}`, `{venue}`, "
        "`{school_name}`."
    )
    certificate_body_template = st.text_area(
        "Custom body text",
        value=existing.certificate_body_template or "" if existing else "",
        key=f"{prefix}_body",
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
    existing_signatories = (existing.signatory_overrides if existing else None) or []
    signatory_overrides = []
    for i in range(3):
        default_sig = existing_signatories[i] if i < len(existing_signatories) else {}
        scol1, scol2 = st.columns(2)
        sig_name = scol1.text_input(
            f"Signatory {i + 1} name", value=default_sig.get("name", ""), key=f"{prefix}_signame_{i}"
        )
        sig_position = scol2.text_input(
            f"Signatory {i + 1} position", value=default_sig.get("position", ""), key=f"{prefix}_sigpos_{i}"
        )
        if sig_name:
            signatory_overrides.append({"name": sig_name, "position": sig_position})

    return {
        "effective_school_year_id": sy_choice,
        "scope": AwardScope(scope),
        "require_complete_record": require_complete_record,
        "require_no_derogatory_record": require_no_derogatory_record,
        "require_no_failed_subject": require_no_failed_subject,
        "require_perfect_attendance": require_perfect_attendance,
        "manual_only": manual_only,
        "min_general_average": min_general_average or None,
        "min_lowest_final_grade": min_lowest_final_grade or None,
        "tier_thresholds": tiers or None,
        "certificate_layout": CertificateLayout(certificate_layout),
        "certificate_body_template": certificate_body_template or None,
        "signatory_overrides": signatory_overrides or None,
    }


def render() -> None:
    current_user = require_role("SUPER_ADMIN")
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

        # Which versions have ever been used to compute or override a
        # learner's award — those are locked against edit/delete (rule 6:
        # no silent recalculation of an award a real learner already
        # received). One grouped query for every policy on the page,
        # not one per version.
        usage_counts = dict(
            session.query(LearnerAward.award_policy_version_id, func.count(LearnerAward.id))
            .group_by(LearnerAward.award_policy_version_id)
            .all()
        )

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
                if v.manual_only:
                    shape = "manual only — no automatic rule, judged by override"
                elif v.tier_thresholds:
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
                    f"{'perfect attendance required, ' if v.require_perfect_attendance else ''}"
                    f"{shape} — effective {sy_by_id.get(v.effective_school_year_id).name if v.effective_school_year_id else '—'}"
                    f"{extras_suffix}"
                )

                used_count = usage_counts.get(v.id, 0)

                status_col, status_btn_col, _spacer = st.columns([2, 1, 3])
                status_options = [s.value for s in PolicyVersionStatus]
                new_status = status_col.selectbox(
                    "Status",
                    options=status_options,
                    index=status_options.index(v.status.value),
                    key=f"status_{v.id}",
                    label_visibility="collapsed",
                )
                if status_btn_col.button(
                    "Update status", key=f"status_btn_{v.id}", disabled=new_status == v.status.value
                ):
                    previous_status = v.status
                    v.status = PolicyVersionStatus(new_status)
                    audit_service.record(
                        session,
                        action=audit_service.AWARD_POLICY_VERSION_STATUS_CHANGED,
                        object_type="award_policy_versions",
                        object_id=v.id,
                        user_id=current_user.id,
                        previous={"status": previous_status},
                        new={"status": v.status},
                    )
                    if try_commit(session, f"v{v.version_number} status set to {new_status}."):
                        st.rerun()

                if used_count:
                    st.caption(
                        f"Locked — used for {used_count} learner award(s) already. "
                        "Create a new version instead of editing; Status above is "
                        "still changeable any time."
                    )
                else:
                    with st.expander(f"Edit v{v.version_number}"):
                        with st.form(f"edit_version_{v.id}"):
                            fields = _render_version_fields(
                                f"editver_{v.id}", school_years, sy_by_id, existing=v
                            )
                            if st.form_submit_button("Save changes"):
                                previous = _version_snapshot(v)
                                for field, value in fields.items():
                                    setattr(v, field, value)
                                was, now = audit_service.changes(previous, fields)
                                if was:
                                    audit_service.record(
                                        session,
                                        action=audit_service.AWARD_POLICY_VERSION_CHANGED,
                                        object_type="award_policy_versions",
                                        object_id=v.id,
                                        user_id=current_user.id,
                                        previous=was,
                                        new=now,
                                    )
                                if try_commit(session, f"Saved v{v.version_number}."):
                                    st.rerun()
                    if st.button(f"Delete v{v.version_number}", key=f"delete_version_{v.id}"):
                        audit_service.record(
                            session,
                            action=audit_service.AWARD_POLICY_VERSION_DELETED,
                            object_type="award_policy_versions",
                            object_id=v.id,
                            user_id=current_user.id,
                            previous=_version_snapshot(v),
                        )
                        if try_delete(session, v, f"v{v.version_number} of {policy.name}"):
                            st.rerun()

            with st.expander(f"Create new version for {policy.name}"):
                with st.form(f"add_version_{policy.id}"):
                    next_version = (versions[0].version_number + 1) if versions else 1
                    st.write(f"Version number: {next_version}")
                    fields = _render_version_fields(f"newver_{policy.id}", school_years, sy_by_id)
                    status = st.selectbox(
                        "Status", options=[s.value for s in PolicyVersionStatus], key=f"status_new_{policy.id}"
                    )
                    if st.form_submit_button("Create version"):
                        new_version = AwardPolicyVersion(
                            award_policy_id=policy.id,
                            version_number=next_version,
                            status=PolicyVersionStatus(status),
                            **fields,
                        )
                        session.add(new_version)
                        # Flushed (not just added) so the new row has an id
                        # to record the audit entry against — same pattern
                        # as gradebook.py's own new-row audit trail.
                        session.flush()
                        audit_service.record(
                            session,
                            action=audit_service.AWARD_POLICY_VERSION_CREATED,
                            object_type="award_policy_versions",
                            object_id=new_version.id,
                            user_id=current_user.id,
                            new={**fields, "version_number": next_version, "status": new_version.status},
                        )
                        try_commit(session, f"Created version {next_version} for {policy.name}.")
                        st.rerun()

            with st.expander(f"Edit {policy.name}"):
                with st.form(f"edit_policy_{policy.id}"):
                    new_name = st.text_input("Name", value=policy.name, key=f"editname_{policy.id}")
                    new_description = st.text_area(
                        "Description", value=policy.description or "", key=f"editdesc_{policy.id}"
                    )
                    if st.form_submit_button("Save policy details"):
                        if not new_name.strip():
                            st.error("Name is required.")
                        else:
                            previous = {"name": policy.name, "description": policy.description}
                            policy.name = new_name.strip()
                            policy.description = new_description.strip() or None
                            was, now = audit_service.changes(
                                previous, {"name": policy.name, "description": policy.description}
                            )
                            if was:
                                audit_service.record(
                                    session,
                                    action=audit_service.AWARD_POLICY_CHANGED,
                                    object_type="award_policies",
                                    object_id=policy.id,
                                    user_id=current_user.id,
                                    previous=was,
                                    new=now,
                                )
                            if try_commit(session, f"Saved {policy.name}."):
                                st.rerun()

            if not versions:
                if st.button(f"Delete {policy.name} (no versions)", key=f"delete_policy_{policy.id}"):
                    audit_service.record(
                        session,
                        action=audit_service.AWARD_POLICY_DELETED,
                        object_type="award_policies",
                        object_id=policy.id,
                        user_id=current_user.id,
                        previous={"name": policy.name, "description": policy.description},
                    )
                    if try_delete(session, policy, policy.name):
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
                elif any(p.name.strip().lower() == name.strip().lower() for p in policies):
                    st.error(
                        f"A policy named “{name}” already exists — add a version to "
                        "it instead of creating a duplicate."
                    )
                else:
                    new_policy = AwardPolicy(name=name, description=description or None)
                    session.add(new_policy)
                    session.flush()
                    audit_service.record(
                        session,
                        action=audit_service.AWARD_POLICY_CREATED,
                        object_type="award_policies",
                        object_id=new_policy.id,
                        user_id=current_user.id,
                        new={"name": new_policy.name, "description": new_policy.description},
                    )
                    if try_commit(session, f"Added {name}."):
                        clear_text_fields("add_award_policy")
                    st.rerun()
