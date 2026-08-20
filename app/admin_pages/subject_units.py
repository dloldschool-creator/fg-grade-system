"""Subject units — the weights behind every Term Average and General
Average (DepEd Order 017 s. 2026, Annex E).

**Why this page exists.** Units are ordinary data, resolved through a
four-level chain, and until this page they could only be changed with SQL.
DepEd revises tables, schools add subjects, and a unit that is wrong does
not raise an error — it produces a slightly different, entirely plausible
average. So the values need to be visible and editable by the person
responsible for them, and every change needs to be attributable.

**The chain, narrowest first**, which is also the order this page shows it:

  1. `section_subject_offerings.units_per_term` — one section teaches a
     subject at unusual hours. Rare; normally empty.
  2. `subjects.units_per_term` — the subject differs from its category.
     This is how one category carries two values: a Tech-Pro elective is 4
     units in Grade 11 and 12 in Grade 12.
  3. `subject_categories.units_per_term` — the Table 19 default.
  4. 1 — nothing configured. Deliberately 1 and not 0: an unconfigured
     subject keeps counting once, rather than vanishing from the average.

**Editing units does not recompute anything.** The summary tables are
caches, rebuilt when a grade is saved, so a unit changed today reaches a
learner's average only when their grades are next recomputed. While no
grades exist that distinction is invisible; once they do, changing a unit
without recomputing leaves a silent split. The page says so, with the
count, rather than leaving it to be discovered.

**Finalized years are never affected.** `learner_academic_records` freezes
the units it used as numbers rather than referencing this table (§38), so
editing here cannot reach a year that has been closed out (rule 6).
"""

import streamlit as st

from app import audit_service
from app.admin_pages._helpers import (
    get_session,
    keep_panel_open,
    panel_is_open,
    render_flashes,
    stateful_tabs,
    try_commit,
)
from app.auth import require_role
from app.curriculum_policy import load_offering_units, resolve_averaging_rules
from app.grading_engine import DEFAULT_UNITS_PER_TERM, AveragingMethod, units_from_hours
from app.models.academic_structure import GradeLevel
from app.models.grades import TermGrade
from app.models.organization import SchoolYear
from app.models.subjects import (
    CombinedLearningArea,
    SectionSubjectOffering,
    Subject,
    SubjectCategory,
)

UNITS_COLUMN = "Units per term"


def _units_column(label: str = UNITS_COLUMN, help_text: str | None = None):
    """One shared column definition, so every table on this page accepts the
    same values. Blank means inherit; `min_value=0` is deliberate — 0 is a
    legal thing to type and is rejected on save with an explanation, which
    is clearer than a widget that silently refuses the keystroke."""
    return st.column_config.NumberColumn(
        label,
        help=help_text or "Blank inherits from the level below. DO 017: core 2, "
        "academic elective 3, arts elective 6, TechPro 4 (G11) / 12 (G12), "
        "work immersion 12.",
        min_value=0.0,
        max_value=99.0,
        step=0.5,
        format="%.4g",
        width="small",
    )


def _as_float(value):
    return float(value) if value is not None else None


def _cell(row, key):
    """One edited cell, normalised to a float or None.

    An emptied numeric cell can come back as None or as NaN depending on
    how the editor round-trips it, and NaN is the dangerous one: it is a
    float, so a naive comparison treats it as a real value and `float(nan)`
    would be written into a NUMERIC column. Both mean "blank" here.
    """
    value = row.get(key)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number  # NaN is the only x != x


def _changed(before, after) -> bool:
    """None and a number differ; two numbers compare numerically, so 2 and
    2.00 do not read as an edit and re-saving an untouched table writes
    nothing."""
    if before is None and after is None:
        return False
    if before is None or after is None:
        return True
    return float(before) != float(after)


def _load(session):
    """Everything the page needs, in a fixed handful of queries. Nothing
    below this line queries per row."""
    categories = session.query(SubjectCategory).order_by(SubjectCategory.code).all()
    subjects = session.query(Subject).order_by(Subject.code).all()
    grade_levels = {g.id: g.code for g in session.query(GradeLevel).all()}
    areas = session.query(CombinedLearningArea).order_by(CombinedLearningArea.name).all()
    offerings = session.query(SectionSubjectOffering).all()
    school_year = (
        session.query(SchoolYear).order_by(SchoolYear.name.desc()).first()
    )
    encoded = (
        session.query(TermGrade).filter(TermGrade.official_grade.isnot(None)).count()
    )
    return {
        "categories": categories,
        "subjects": subjects,
        "grade_levels": grade_levels,
        "areas": areas,
        "offerings": offerings,
        "school_year": school_year,
        "encoded": encoded,
        "units": load_offering_units(session, offerings),
    }


def _in_force(session, context) -> None:
    """What the units currently add up to, and whether they are being used
    at all — a page full of unit values is misleading if the policy in
    force still averages flat."""
    school_year = context["school_year"]
    if school_year is None:
        st.warning("No school year yet — set one up before configuring units.")
        return

    levels = sorted(context["grade_levels"].items(), key=lambda kv: kv[1])
    columns = st.columns(max(len(levels), 1))
    any_weighted = False
    for column, (grade_level_id, code) in zip(columns, levels):
        rules = resolve_averaging_rules(session, school_year.id, grade_level_id)
        weighted = rules.method is AveragingMethod.UNIT_WEIGHTED
        any_weighted = any_weighted or weighted
        column.metric(
            f"{code} averaging",
            "Unit-weighted" if weighted else "Flat average",
        )
        column.caption(
            "Language pair counted once"
            if rules.combine_language_pair_in_term_average
            else "Language pair counted separately"
        )

    if not any_weighted:
        st.info(
            "No grade level is using unit weighting yet, so the values below are "
            "recorded but not applied. Averaging method is set on the Grading "
            "Policy, not here."
        )

    unresolved = sum(1 for u in context["units"].values() if u == DEFAULT_UNITS_PER_TERM)
    if unresolved:
        st.warning(
            f"{unresolved} of {len(context['units'])} subject offerings have no units "
            "configured and are counting as 1. Set them below — a subject weighing 1 "
            "against a core subject's 2 is not an error anything will report."
        )
    else:
        st.success(
            f"All {len(context['units'])} subject offerings resolve to a configured "
            "unit value."
        )

    if context["encoded"]:
        st.warning(
            f"**{context['encoded']} grades are already encoded.** Changing a unit "
            "here does not rebuild averages that were already computed — they are "
            "caches. After editing, run "
            "`python -m scripts.apply_do17_units --recompute --confirm` outside "
            "encoding hours, or those learners keep their old averages while "
            "anyone graded later gets the new ones."
        )


def _category_tab(session, context, user) -> None:
    st.caption(
        "The default for every subject in the classification. Leave blank where "
        "DO 017 does not settle it from the category alone — Tech-Pro Electives "
        "(4 units in Grade 11, 12 in Grade 12) and the Field Exposure / Arts "
        "cluster (80-hour subjects at 3, 160-hour at 6) are both set per subject."
    )
    categories = context["categories"]
    rows = [
        {
            "Code": c.code,
            "Classification": c.name,
            UNITS_COLUMN: _as_float(c.units_per_term),
        }
        for c in categories
    ]
    edited = st.data_editor(
        rows,
        key="units_categories",
        disabled=["Code", "Classification"],
        column_config={
            "Code": st.column_config.TextColumn(width="medium"),
            "Classification": st.column_config.TextColumn(width="large"),
            UNITS_COLUMN: _units_column(),
        },
        hide_index=True,
        width="stretch",
    )
    if st.button("Save category defaults", type="primary", key="save_categories"):
        _save(session, user, zip(categories, edited), "subject_category", lambda c: c.code)


def _subject_tab(session, context, user) -> None:
    st.caption(
        "Overrides the subject's category. Blank means inherit. The **Effective** "
        "column shows what the subject actually weighs once the chain is resolved, "
        "so a blank row is not a gap — it is a subject taking its category's value."
    )
    subjects = context["subjects"]
    grade_levels = context["grade_levels"]
    categories = {c.id: c for c in context["categories"]}

    level_choice = st.selectbox(
        "Grade level",
        options=["All"] + sorted(set(grade_levels.values())),
        key="units_subject_level",
    )
    visible = [
        s
        for s in subjects
        if level_choice == "All" or grade_levels.get(s.grade_level_id) == level_choice
    ]
    if not visible:
        st.info("No subjects for that grade level.")
        return

    rows = []
    for subject in visible:
        category = categories.get(subject.subject_category_id)
        override = _as_float(subject.units_per_term)
        inherited = _as_float(category.units_per_term) if category else None
        effective = override if override is not None else inherited
        rows.append(
            {
                "Code": subject.code,
                "Subject": subject.official_name,
                "Grade": grade_levels.get(subject.grade_level_id, "—"),
                "Category": category.code if category else "—",
                "Hours/year": subject.instructional_hours_per_year,
                UNITS_COLUMN: override,
                "Effective": effective if effective is not None else 1.0,
            }
        )
    edited = st.data_editor(
        rows,
        key=f"units_subjects_{level_choice}",
        disabled=["Code", "Subject", "Grade", "Category", "Effective"],
        column_config={
            "Code": st.column_config.TextColumn(width="small"),
            "Subject": st.column_config.TextColumn(width="large"),
            "Grade": st.column_config.TextColumn(width="small"),
            "Category": st.column_config.TextColumn(width="medium"),
            "Hours/year": st.column_config.NumberColumn(
                help="DO 017's prescribed hours. Recorded for reference and used "
                "to suggest units below; never read while grading.",
                min_value=0,
                max_value=2000,
                step=10,
                width="small",
            ),
            UNITS_COLUMN: _units_column("Override"),
            "Effective": st.column_config.NumberColumn(
                help="What this subject actually weighs per term after the chain "
                "resolves. 1 means nothing is configured.",
                format="%.4g",
                width="small",
            ),
        },
        hide_index=True,
        width="stretch",
    )
    if st.button("Save subject overrides", type="primary", key="save_subjects"):
        _save(
            session,
            user,
            zip(visible, edited),
            "subject",
            lambda s: s.code,
            hours_field=True,
        )

    _hours_helper(visible)


def _hours_helper(subjects) -> None:
    """Turns prescribed hours into the Table 19 unit value.

    Every row of Table 19 is the same rate — 3 units per 80 hours in a term
    — so a subject DepEd adds later needs its hours, not a rule. Offered as
    a calculator rather than applied automatically: how many terms a subject
    runs is a property of the section's offerings, not of the catalog, and
    guessing it is exactly the kind of plausible-but-wrong this page is
    trying to prevent.
    """
    # Both boxes live outside a form, so each one reruns the script the
    # moment it changes — which rebuilds the expander closed, taking the
    # answer with it. `panel_is_open`/`keep_panel_open` is the standing fix
    # (tests/test_expander_state.py), and a calculator that shuts itself the
    # instant you type in it would be worse than not having one.
    panel = "units_hours_calculator"
    with st.expander("Work out units from prescribed hours", expanded=panel_is_open(panel)):
        left, right = st.columns(2)
        hours = left.number_input(
            "Hours per year", min_value=0, max_value=2000, value=160, step=10,
            key="units_calc_hours", on_change=keep_panel_open, args=(panel,),
        )
        terms = right.number_input(
            "Terms the subject runs in", min_value=1, max_value=6, value=3, step=1,
            key="units_calc_terms", on_change=keep_panel_open, args=(panel,),
        )
        suggested = units_from_hours(hours, terms)
        if suggested is not None:
            st.write(
                f"**{float(suggested):.4g} units per term**, "
                f"{float(suggested) * terms:.4g} for the year "
                f"({hours} hours over {terms} term(s))."
            )


def _combined_tab(session, context, user) -> None:
    st.caption(
        "A combined learning area is weighted as ONE subject — deliberately not "
        "the sum of its components, which would weight the languages twice. Blank "
        "falls back to one component's units."
    )
    areas = context["areas"]
    if not areas:
        st.info("No combined learning areas configured.")
        return
    rows = [
        {"Learning area": a.name, UNITS_COLUMN: _as_float(a.units_per_term)}
        for a in areas
    ]
    edited = st.data_editor(
        rows,
        key="units_areas",
        disabled=["Learning area"],
        column_config={
            "Learning area": st.column_config.TextColumn(width="large"),
            UNITS_COLUMN: _units_column(),
        },
        hide_index=True,
        width="stretch",
    )
    if st.button("Save combined areas", type="primary", key="save_areas"):
        _save(session, user, zip(areas, edited), "combined_learning_area", lambda a: a.name)


def _save(session, user, pairs, object_type: str, label, hours_field: bool = False) -> None:
    """Writes only what actually changed, auditing each one.

    Rejects 0 outright. It is a number someone will type meaning "don't
    count this", and it would instead remove the subject from the
    denominator of every average silently — blank is how you say inherit,
    and there is no way to say weightless.
    """
    changes = 0
    for record, row in pairs:
        new_units = _cell(row, UNITS_COLUMN)
        if new_units is not None and new_units == 0:
            st.error(
                f"{label(record)}: 0 units would drop the subject out of every "
                "average without a trace. Leave the cell blank to inherit instead."
            )
            return

        updates = {}
        if _changed(record.units_per_term, new_units):
            updates["units_per_term"] = (_as_float(record.units_per_term), new_units)
        if hours_field:
            new_hours = _cell(row, "Hours/year")
            if _changed(record.instructional_hours_per_year, new_hours):
                updates["instructional_hours_per_year"] = (
                    record.instructional_hours_per_year,
                    int(new_hours) if new_hours is not None else None,
                )
        if not updates:
            continue

        for field, (_before, after) in updates.items():
            setattr(record, field, after)
        audit_service.record(
            session,
            action=audit_service.SUBJECT_UNITS_CHANGED,
            object_type=object_type,
            object_id=record.id,
            user_id=user.id,
            previous={f: before for f, (before, _a) in updates.items()},
            new={f: after for f, (_b, after) in updates.items()},
        )
        changes += 1

    if not changes:
        st.info("Nothing changed.")
        return
    if try_commit(session, f"Updated {changes} row(s)."):
        st.rerun()


def render() -> None:
    current_user = require_role("SUPER_ADMIN")
    st.title("Subject Units")
    st.caption(
        "The weight each subject carries in a Term Average and a General Average "
        "(DepEd Order 017 s. 2026, Annex E). A subject with the wrong units still "
        "produces a believable average, so these are worth checking rather than "
        "assuming."
    )
    render_flashes()

    with get_session() as session:
        context = _load(session)
        _in_force(session, context)
        st.divider()

        tab = stateful_tabs(
            "subject_units_tab",
            ["Category defaults", "Per-subject overrides", "Combined areas"],
        )
        if tab == "Category defaults":
            _category_tab(session, context, current_user)
        elif tab == "Per-subject overrides":
            _subject_tab(session, context, current_user)
        else:
            _combined_tab(session, context, current_user)
