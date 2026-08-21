"""An adviser edits their own learners, and looks up everyone else's.

**The hole this closes.** §3C gives a Class Adviser "manage learners in
assigned section" and §54 states the consequence outright — "Adviser sees
learners only in assigned sections unless additionally authorized". Every
other adviser-facing page reaches its learners through
`section_picker(adviser_user_id=...)`. The Learner Masterlist queried
`learners` directly and scoped only the *enrollment* widgets, so any of
the school's advisers could open any of its ~1,200 learners and retype a
name, a birthdate or an LRN. Nothing about the page looked wrong; the
scoping that was there read as the scoping that was needed.

**Why the lookup survives the scoping.** `learners.lrn` is uniquely
indexed and an LRN is copied off a paper form by hand. An adviser who
cannot search the school cannot discover that a transferee is already in
it, and enters them a second time — trading a privacy gain for a
data-integrity loss on the one field §54 protects hardest. So a search
still reaches everyone; what it returns for a stranger is a card, not a
form.

The rule is unit-tested where it is pure and read against the live
database where it is not, in the manner of
`tests/test_section_profile_default.py`: a rule about who may edit real
people is worth checking against the real sections.
"""

import ast
import inspect
import pathlib

import pytest

from app.admin_pages import learners as page
from app.learner_access import editable_learner_ids, may_edit

PAGE_SOURCE = pathlib.Path(inspect.getfile(page)).read_text(encoding="utf-8")


# --- may_edit, the guard that sits next to each write ----------------------


def test_an_unscoped_account_may_edit_anything():
    """`adviser_user_id` is None for a Registrar or Super Admin, and they
    are not scoped by section at all."""
    assert may_edit("any-learner", set(), None)


def test_an_adviser_may_edit_a_learner_in_their_set():
    assert may_edit("learner-1", {"learner-1", "learner-2"}, "adviser-1")


def test_an_adviser_may_not_edit_a_learner_outside_it():
    assert not may_edit("learner-9", {"learner-1"}, "adviser-1")


def test_an_adviser_with_no_sections_may_edit_nothing():
    """An empty set is the honest answer for a teacher who advises
    nothing yet, and it must not read as 'unrestricted'."""
    assert not may_edit("learner-1", set(), "adviser-1")


def test_editable_ids_does_not_query_for_an_unscoped_account():
    """Calling it for a Registrar would be two wasted round trips on a
    page that already costs several, and an empty set from here must
    never be mistaken for 'they may edit nothing' — `may_edit` reads the
    None instead."""

    class Exploding:
        def query(self, *a, **k):
            raise AssertionError("queried the database for an unscoped account")

    assert editable_learner_ids(Exploding(), None) == set()


# --- What the page draws, and for whom -------------------------------------


def _function(name):
    return ast.parse(inspect.getsource(getattr(page, name)).strip())


def _rendered_widgets(tree):
    """Streamlit calls under `tree` that accept input from the user."""
    inputs = {
        "text_input", "text_area", "number_input", "date_input", "selectbox",
        "checkbox", "multiselect", "file_uploader", "form_submit_button",
        "button", "toggle", "slider", "data_editor",
    }
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in inputs
    }


def test_the_read_only_card_has_nothing_to_type_into():
    """The whole difference between the two halves of this page. A card
    that grew a text box would look like a small convenience and would be
    the rule quietly coming undone."""
    assert _rendered_widgets(_function("_read_only_card")) == set()


def test_the_read_only_card_still_names_the_learner_and_the_lrn():
    """Read-only is not the same as useless — searching before adding is
    the point, and an LRN that isn't shown can't be checked against the
    form in the adviser's hand."""
    source = inspect.getsource(page._read_only_card)
    assert "learner.lrn" in source
    assert "learner.last_name" in source


def test_the_read_only_card_names_who_can_make_the_change():
    """"You can't edit this" with no next step is where a teacher stops
    and the office hears about it a week later."""
    source = inspect.getsource(page._read_only_card)
    assert "adviser" in source.lower() or "registrar" in source.lower()
    assert "adviser" in inspect.getsource(page._placements)


def test_only_the_editable_learners_get_an_edit_form():
    """`mine` is the filtered list; the forms hang off it and the cards
    off `others`. Drawing the forms from `learners` would put the whole
    search result back into edit."""
    render = inspect.getsource(page.render)
    assert "for learner in mine:" in render
    assert "_identity_form(session, learner" in render
    assert "for learner in sorted(others, key=learner_sort_key):" in render
    # The split itself, and that it is `may_edit` making it rather than a
    # second copy of the rule written inline.
    assert "may_edit(learner.id, editable, adviser_user_id)" in render


def test_the_page_asks_the_access_rule_rather_than_rebuilding_it():
    """One implementation, in `app.learner_access`. A page-local
    `filter_by(adviser_user_id=...)` would drift from it the first time
    the rule gained a second half — which it already has."""
    assert "from app.learner_access import editable_learner_ids, may_edit" in PAGE_SOURCE


def test_delete_is_registrar_only():
    """The database refuses to delete an *enrolled* learner (every
    foreign key here is ON DELETE RESTRICT), so this button only ever
    bit on learners with no enrollment — the just-imported, not-yet-
    enrolled set, which is the one somebody is most likely to be halfway
    through typing."""
    render = inspect.getsource(page.render)
    assert 'may_delete = current_user.has_role("SUPER_ADMIN", "REGISTRAR")' in render
    assert "may_delete=may_delete" in render

    form = inspect.getsource(page._identity_form)
    assert 'if may_delete and columns[1].form_submit_button("Delete")' in form


def test_the_delete_button_is_absent_rather_than_disabled_for_an_adviser():
    """A greyed-out Delete is a question the page can't answer well, and
    a disabled widget is one keyword away from being enabled again.

    Read from the AST, not the text, so the comment explaining the rule
    can't be what trips it."""
    disabled = [
        node
        for node in ast.walk(_function("_identity_form"))
        if isinstance(node, ast.Call)
        and any(keyword.arg == "disabled" for keyword in node.keywords)
    ]
    assert not disabled


# --- Creation is attributed, so a bulk add stays fixable --------------------


def test_the_add_form_stamps_the_creator():
    """Without it, adding a learner and leaving the section blank puts
    them beyond the reach of the person who just typed them: nobody
    advises a learner who isn't enrolled anywhere."""
    render = inspect.getsource(page.render)
    assert "created_by_user_id=current_user.id" in render


def test_the_bulk_importer_stamps_the_creator_too():
    """The sharper case. The bulk panel refuses a Section the uploader
    does not advise **and still creates the learners** — deliberately,
    since refusing them outright is worse. Those rows land enrolled
    nowhere, and the stamp is the only thing that keeps them editable."""
    from app.import_specs import commit_learners

    source = inspect.getsource(commit_learners)
    assert "created_by_user_id=user_id" in source


def test_the_importer_does_not_write_an_audit_row_per_learner():
    """A 1,200-learner migration would put 1,200 identical entries into a
    viewer that shows the most recent 200, burying every other kind of
    change behind one afternoon. The import is already recorded once, as
    DATA_IMPORTED, and the creator is a column."""
    from app.import_specs import commit_learners

    assert "audit_service" not in inspect.getsource(commit_learners)


# --- Every write is attributable (rule 8, §50) -----------------------------


def _audit_actions(tree):
    return {
        keyword.value.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "record"
        for keyword in node.keywords
        if keyword.arg == "action" and isinstance(keyword.value, ast.Attribute)
    }


def test_identity_edits_and_deletes_are_logged():
    """A learner's name, sex, birthdate and LRN are the identity every
    report the school issues is printed under, and none of the four was
    logged at all before 2026-08-21. Scoping stops the wrong person
    editing; this catches the right person editing wrongly, which is the
    commoner failure."""
    assert _audit_actions(_function("_identity_form")) == {
        "LEARNER_CHANGED",
        "LEARNER_DELETED",
    }


def test_creation_and_the_admission_record_are_logged():
    assert "LEARNER_CREATED" in _audit_actions(_function("render"))
    assert _audit_actions(_function("_admission_record_form")) == {
        "LEARNER_ADMISSION_CHANGED"
    }


def test_an_edit_that_changed_nothing_writes_no_entry():
    """Opening a panel and pressing Save is how people check a record.
    Logging that as a change fills the log with entries that describe
    nothing and makes the real ones harder to find."""
    form = inspect.getsource(page._identity_form)
    assert "was, now = audit_service.changes(" in form
    assert "if was:" in form


def test_the_delete_entry_is_written_before_the_row_goes():
    """`audit_service.record` appends to the caller's session on purpose,
    so the entry and the change share a transaction — and a delete the
    database refuses takes its own audit row back with it."""
    form = inspect.getsource(page._identity_form)
    assert form.index("LEARNER_DELETED") < form.index("try_delete(session, learner")


def test_the_new_actions_are_visible_in_the_audit_viewer():
    """An action the filter doesn't list is an action nobody finds."""
    from app.admin_pages import audit_log

    for action in (
        "LEARNER_CREATED",
        "LEARNER_CHANGED",
        "LEARNER_DELETED",
        "LEARNER_ADMISSION_CHANGED",
    ):
        assert action in audit_log.ALL_ACTIONS


# --- Cost, because this page draws one panel per learner -------------------


def test_the_access_query_runs_once_per_render_not_once_per_learner():
    """Streamlit re-runs the whole script on every interaction and this
    page draws an expander per learner. `editable_learner_ids` is called
    from `render` and never from inside a loop or a panel."""
    tree = _function("render")
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "editable_learner_ids"
    ]
    assert len(calls) == 1

    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While)):
            assert not [
                inner
                for inner in ast.walk(node)
                if isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id in ("editable_learner_ids", "_placements")
            ], "the access lookup moved inside a loop"


def test_placements_are_batched_above_the_read_only_cards():
    """Four queries for the whole search result, not four per stranger
    on it. The card loop holds no session call at all — which is also
    what keeps `tests/test_expander_cost.py` honest here."""
    source = inspect.getsource(page._placements)
    assert source.count("session.query") == 4
    assert "session.get" not in source

    # The loop body itself, taken from the AST — slicing the source by
    # text runs on into the Add learner form below it, which queries
    # perfectly legitimately.
    for node in ast.walk(_function("render")):
        if (
            isinstance(node, ast.For)
            and isinstance(node.iter, ast.Call)
            and getattr(node.iter.func, "id", None) == "sorted"
        ):
            names = {
                inner.value.id
                for inner in ast.walk(node)
                if isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name)
            }
            assert "session" not in names
            break
    else:
        raise AssertionError("the read-only card loop is no longer there to check")


def test_an_advisers_own_list_is_not_truncated():
    """`RESULT_LIMIT` is there so an empty search doesn't render the
    school. An adviser's own set is already bounded by the sections they
    hold, and cutting it at fifty would hide learners from the only page
    that lets their adviser correct them — an adviser holding two SNED
    sections is over the limit."""
    listed = inspect.getsource(page._listed_learners)
    adviser_branch = listed[listed.index("if adviser_user_id is not None:"):]
    adviser_branch = adviser_branch[: adviser_branch.index("return (\n        session.query(Learner)\n        .order_by(Learner.last_name")]
    assert "RESULT_LIMIT" not in adviser_branch


def test_an_advisers_own_list_is_in_roster_order():
    """Male first, then female, alphabetical within each — the order
    every DepEd form and the teachers' own workbook use. Ordering on
    `Learner.sex` directly puts FEMALE first, because the stored strings
    are "MALE" and "FEMALE"."""
    listed = inspect.getsource(page._listed_learners)
    assert "learner_order_by(Learner)" in listed
    assert ".order_by(Learner.sex" not in listed


# --- Against the live sections, because the rule is about real people ------
#
# Read-only: these query, they never write. The structural tests above
# hold whatever is in the database; these are what notice a section
# renamed into ambiguity or an adviser whose scope has quietly become
# the whole school.


@pytest.fixture
def session():
    from app.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _advisers_holding_sections(session, limit=3):
    from app.models.academic_structure import Section

    # Ordered, so the same advisers are picked on every run and a failure
    # can be reproduced rather than re-rolled.
    seen, out = set(), []
    for section in (
        session.query(Section)
        .filter(Section.adviser_user_id.isnot(None))
        .order_by(Section.name)
        .all()
    ):
        if section.adviser_user_id not in seen:
            seen.add(section.adviser_user_id)
            out.append(section.adviser_user_id)
        if len(out) >= limit:
            break
    return out


def test_every_editable_learner_is_one_the_adviser_can_account_for(session):
    """Computed independently of `editable_learner_ids`: enrolled in a
    section they advise, or created by them and enrolled nowhere."""
    from app.models.academic_structure import Section
    from app.models.learners import Enrollment, Learner

    advisers = _advisers_holding_sections(session)
    if not advisers:
        pytest.skip("no section carries an adviser in this database")

    for adviser_id in advisers:
        mine = editable_learner_ids(session, adviser_id)
        section_ids = {
            row[0]
            for row in session.query(Section.id)
            .filter(Section.adviser_user_id == adviser_id)
            .all()
        }
        for learner_id in mine:
            enrollments = (
                session.query(Enrollment).filter(Enrollment.learner_id == learner_id).all()
            )
            if enrollments:
                assert any(e.section_id in section_ids for e in enrollments), (
                    f"{learner_id} is editable but enrolled only outside "
                    f"{adviser_id}'s sections"
                )
            else:
                learner = session.get(Learner, learner_id)
                assert str(learner.created_by_user_id) == str(adviser_id), (
                    f"{learner_id} is editable, enrolled nowhere, and not theirs"
                )


def test_no_adviser_is_handed_the_whole_school(session):
    """The regression in one line. Before this rule existed the page put
    every learner in the database in front of every adviser."""
    from app.models.learners import Learner

    advisers = _advisers_holding_sections(session)
    if not advisers:
        pytest.skip("no section carries an adviser in this database")

    total = session.query(Learner).count()
    if total < 2:
        pytest.skip("not enough learners for the comparison to mean anything")

    for adviser_id in advisers:
        assert len(editable_learner_ids(session, adviser_id)) < total


def test_a_learner_in_another_advisers_section_is_out_of_scope(session):
    """Two advisers, and neither one's roster leaks into the other's."""
    from app.models.academic_structure import Section
    from app.models.learners import Enrollment

    advisers = _advisers_holding_sections(session, limit=2)
    if len(advisers) < 2:
        pytest.skip("fewer than two advisers hold sections in this database")

    first, second = advisers
    theirs = {
        row[0]
        for row in session.query(Enrollment.learner_id)
        .filter(
            Enrollment.section_id.in_(
                session.query(Section.id).filter(Section.adviser_user_id == second)
            )
        )
        .all()
    }
    if not theirs:
        pytest.skip("the second adviser's sections are empty")

    assert not (editable_learner_ids(session, first) & theirs)


def test_the_read_only_group_still_says_what_it_is():
    """It has no heading — the matches above it are search results too and
    carry none, so labelling only this half would read as though the
    editable half were something else. That makes the caption the only
    thing telling an adviser why these rows can't be typed into, and the
    only thing naming who can change them. Easy to tidy away; not easy to
    notice missing."""
    render = inspect.getsource(page.render)
    branch = render[render.index("if others:"):render.index("for learner in sorted(others")]
    assert "st.caption(" in branch
    assert "read-only" in branch
    assert "registrar" in branch


def test_the_read_only_group_draws_no_rule_when_it_is_the_only_group():
    """The case the feature exists for: an adviser searches an LRN to see
    whether a transferee is already in the school, and matches none of
    their own. An unconditional divider then puts a horizontal line
    directly under the search box, which reads as a rendering fault
    rather than as a separator between two things."""
    render = inspect.getsource(page.render)
    branch = render[render.index("if others:"):render.index("for learner in sorted(others")]
    assert "if mine:\n                st.divider()" in branch
