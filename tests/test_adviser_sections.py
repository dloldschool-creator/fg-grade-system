"""An adviser may hold more than one section in a school year.

The school runs SNED sections — 4 in Grade 11, 3 in Grade 12 — and one
Grade 11 adviser holds two of them: same strand, same subjects, same
room, 5 and 7 learners. A unique index refused it.

The rule it enforced had no source. `14e55ba4624b` is bare autogenerate,
"please adjust!" and all, and the model comment explained only why the
index was scoped and partial — never why one section per adviser should
be true. §3C says an adviser sees learners in assigned *sections*.

What replaces it is a **warning, not a refusal**: picking the wrong name
out of a dropdown of forty is a real mistake, and catching that was the
one thing the index was genuinely good for.
"""

import ast
import inspect
import pathlib
from types import SimpleNamespace

from app.admin_pages import sections as sections_page
from app.models.academic_structure import Section

ALEMBIC = pathlib.Path(__file__).resolve().parent.parent / "alembic" / "versions"


def _section(name, adviser, grade="G11", id=None):
    return SimpleNamespace(
        id=id or name, name=name, adviser_user_id=adviser, grade_level_id=grade
    )


GRADES = {"G11": SimpleNamespace(code="G11"), "G12": SimpleNamespace(code="G12")}


# --- The constraint is gone, and stays gone --------------------------------


def test_the_model_declares_no_uniqueness_on_adviser():
    """A re-added constraint would fail at the next `alembic upgrade`, on
    a school where two SNED sections legitimately share an adviser."""
    names = {
        getattr(arg, "name", None) for arg in Section.__table_args__
    } | {
        index.name for index in Section.__table__.indexes
    }
    assert "uq_sections_adviser_per_school_year" not in names
    assert not any(
        set(index.columns.keys()) == {"school_year_id", "adviser_user_id"}
        and index.unique
        for index in Section.__table__.indexes
    )


def test_section_names_are_still_unique_per_grade_level():
    """The other constraint on this table is untouched — two SNED
    sections need two names."""
    constraints = {
        tuple(arg.columns.keys())
        for arg in Section.__table_args__
        if hasattr(arg, "columns")
    }
    assert ("school_year_id", "grade_level_id", "name") in constraints


def test_the_migration_refuses_to_restore_the_rule_over_data_that_breaks_it():
    """`downgrade` recreates a unique index, which fails if an adviser has
    since gained a second section. That is correct — silently choosing one
    to strip would be worse than refusing."""
    source = next(
        p.read_text(encoding="utf-8")
        for p in ALEMBIC.glob("*.py")
        if "adviser_may_hold_more_than_one" in p.name
    )
    assert "drop_index" in source
    assert "create_index" in source.split("def downgrade")[1]


# --- The warning that replaces it ------------------------------------------


def test_no_warning_when_the_adviser_holds_nothing_else():
    assert sections_page._also_advises(
        [_section("GATES", "teacher-1"), _section("BEZOS", None)], "teacher-2"
    ) == []


def test_no_warning_for_an_unassigned_section():
    """`— none —` is the default on every new section; warning about it
    would fire constantly and be ignored, which is how a warning dies."""
    assert sections_page._also_advises([_section("GATES", None)], None) == []


def test_the_section_being_edited_is_not_reported_against_itself():
    """Opening a section and saving it unchanged must not accuse its own
    adviser of double-booking."""
    sned_a = _section("SNED-A", "teacher-1", id="a")
    assert sections_page._also_advises([sned_a], "teacher-1", excluding="a") == []


def test_the_other_sections_are_named():
    """"Already advises another section" is not actionable; the point is
    to show *which*, so a wrong pick is obvious at a glance."""
    held = sections_page._also_advises(
        [
            _section("SNED-A", "teacher-1", id="a"),
            _section("SNED-B", "teacher-1", id="b"),
            _section("GATES", "teacher-2", id="c"),
        ],
        "teacher-1",
        excluding="b",
    )
    assert [s.name for s in held] == ["SNED-A"]


def test_the_sned_case_warns_rather_than_blocks():
    """The whole point: two sections, one adviser, allowed — and said out
    loud so it reads as deliberate rather than as an error nobody saw."""
    source = inspect.getsource(sections_page._warn_if_already_advising)
    assert "st.warning" in source
    assert "st.error" not in source and "st.stop" not in source
    assert "That's allowed" in source


# --- Where the widget sits, which is what makes the warning useful ---------


def test_the_adviser_picker_is_outside_the_form():
    """`st.form` only reruns the script on submit, so an adviser chosen
    inside one would be warned about a click late — after the save the
    warning existed to question. Track already lives outside for exactly
    this reason.
    """
    tree = ast.parse(inspect.getsource(sections_page.render).strip())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.With) and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "form"
            for item in node.items
        )):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "selectbox"
            ):
                label = inner.args[0] if inner.args else None
                assert not (
                    isinstance(label, ast.Constant) and label.value == "Adviser"
                ), "the Adviser picker is inside st.form — the warning will lag a click"


def test_the_warning_costs_no_query():
    """It takes the section list the page already loaded. Streamlit
    re-runs this page on every keystroke in a filter box, and it draws one
    panel per section."""
    source = inspect.getsource(sections_page._also_advises)
    assert "query" not in source and "session" not in source


# --- Two bugs the warning itself introduced, found in use ------------------


def test_the_panel_stays_open_while_an_adviser_change_is_unsaved():
    """Reported straight away: choosing an adviser on a second section
    collapsed the panel, so the warning had to be hunted for by reopening
    it, and the Save button went with it.

    `st.expander` has no memory — every rerun rebuilds it closed — and
    moving the picker outside the form is exactly what started causing
    reruns mid-edit. So the panel is held open while the picker disagrees
    with the database.
    """
    assert sections_page._panel_should_stay_open("teacher-2", "teacher-1") is True


def test_an_untouched_panel_is_not_forced_open():
    """Every section draws one of these. Holding them all open would make
    a 33-section list unusable."""
    assert sections_page._panel_should_stay_open(sections_page._UNSET, "teacher-1") is False
    assert sections_page._panel_should_stay_open(sections_page._UNSET, None) is False


def test_a_saved_change_lets_the_panel_close_again():
    """After Save the picker and the database agree, so it closes by
    itself — which reads as "done" rather than as the same bug."""
    assert sections_page._panel_should_stay_open("teacher-2", "teacher-2") is False


def test_removing_an_adviser_counts_as_a_change():
    """Setting someone back to "— none —" is a real edit. A plain
    truthiness check would treat it as nothing chosen and shut the panel
    on the way to saving it."""
    assert sections_page._panel_should_stay_open(None, "teacher-1") is True


def test_adding_a_section_does_not_warn_about_the_section_it_just_created():
    """The other report: add a section with an adviser, save, and the page
    immediately warned that they already advise a section — naming the one
    just created, on the first assignment that teacher had ever had.

    The picker kept its value while `sections` was re-queried and now
    included the new row. A warning that fires on its own result is how
    people learn to ignore warnings, so Add resets the picker.
    """
    source = inspect.getsource(sections_page.render)
    add_block = source[source.index('st.subheader("Add section")'):]
    assert "_NEW_ADVISER_GENERATION" in add_block
    # A generation in the key, not a deleted session_state entry: this
    # repo has already shipped the version that clears the server's copy
    # while the browser re-sends the old one.
    assert 'key=f"new_sec_adviser#{generation}"' in add_block
    assert "st.session_state[_NEW_ADVISER_GENERATION] = generation + 1" in add_block


def test_the_reset_only_happens_when_the_section_was_really_added():
    """Bumping the generation on a failed commit would blank the adviser
    the user picked while leaving them to retype everything else."""
    source = inspect.getsource(sections_page.render)
    add_block = source[source.index('st.subheader("Add section")'):]
    guard = add_block.index("if try_commit(session,")
    bump = add_block.index("st.session_state[_NEW_ADVISER_GENERATION]")
    assert guard < bump, "the reset must sit inside the try_commit success branch"
