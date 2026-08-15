"""An add form blanks its text boxes on success — and nothing else.

Adding rows is repetitive: the grade level, the track and the term are the
same for the next row, while the code and the name are exactly what must
not sit there to be submitted twice. So `clear_text_fields` empties the
typed boxes and leaves the pickers set.

This is tested through Streamlit's own runtime (`AppTest`) rather than by
reading the source, because the whole feature *is* widget lifecycle.

**Read this before trusting a green run here.** The first implementation
deleted each box's `session_state` key. Every test below passed, and the
boxes did not clear in a browser: a widget inside `st.form` also holds a
value in the *frontend*, which the form keeps across the rerun and
re-submits, so the server believed the box was empty while it still read
"STEM - A" on screen. AppTest has no browser and cannot see that — it
only ever showed the server's side of the disagreement.

What actually clears a box is `clear_text_fields` bumping the form's
generation, which gives every text box a key Streamlit has never seen;
a brand-new widget has nothing to restore, frontend included. So the
generation test below is the one carrying the weight, and a change to
this mechanism needs a real browser, not another assertion here.

requirements.txt pins streamlit>=1.38,<2.0, so a minor upgrade reaching
the host could change this. Then these fail, which is the point.
"""

import streamlit as st
from streamlit.testing.v1 import AppTest

# Kept in the same shape as a real add form: text boxes, a tick box and a
# picker, all inside one st.form, committing through a success flag.
ADD_FORM_SCRIPT = """
import streamlit as st
from app.admin_pages._helpers import clear_text_fields, render_flashes, text_field, flash

render_flashes()
succeeds = st.session_state.get("succeeds", True)

with st.form("add_thing"):
    code = text_field("Code", key="add_thing.code")
    note = text_field("Note", key="add_thing.note", area=True)
    col1, col2 = st.columns(2)
    room = text_field("Room", key="add_thing.room", container=col1)
    active = st.checkbox("Active", key="add_thing.active")
    grade = st.selectbox("Grade level", options=["Grade 11", "Grade 12"], key="add_thing.grade")
    other = st.text_input("Untracked", key="untracked_box")
    if st.form_submit_button("Add"):
        if succeeds:
            flash("success", f"Added {code}.")
            clear_text_fields("add_thing")
        else:
            flash("error", "Couldn't save — that code is already used.")
        st.rerun()
"""


def box(at: AppTest, label: str):
    """Look a text box up by its label, not its key.

    text_field's keys carry the form's generation ("add_thing.code#0"),
    which is the whole mechanism — so a test that addressed boxes by key
    would have to know the generation it is asserting about.
    """
    for widget in list(at.text_input) + list(at.text_area):
        if widget.label == label:
            return widget
    raise AssertionError(f"no text box labelled {label!r}")


def filled_form(succeeds: bool = True) -> AppTest:
    """An add form with every widget filled in, submitted once."""
    at = AppTest.from_string(ADD_FORM_SCRIPT, default_timeout=30)
    at.session_state["succeeds"] = succeeds
    at.run()
    box(at, "Code").set_value("ORAL-COMM")
    box(at, "Note").set_value("three terms")
    box(at, "Room").set_value("Room 12")
    at.checkbox("add_thing.active").set_value(True)
    at.selectbox("add_thing.grade").set_value("Grade 12")
    box(at, "Untracked").set_value("left alone")
    return at.button[0].click().run()


# --- What a successful add clears ------------------------------------------


def test_the_text_boxes_come_back_blank():
    at = filled_form()

    assert box(at, "Code").value == ""
    assert box(at, "Note").value == ""


def test_a_box_built_inside_a_column_clears_too():
    """Learners and Enrollment lay their add forms out in columns, which
    means text_field(container=col1) rather than a bare st.text_input."""
    at = filled_form()

    assert box(at, "Room").value == ""


def test_the_cleared_boxes_are_new_widgets_to_streamlit():
    """The assertion that actually corresponds to what a user sees.

    Clearing works by identity: after a successful add, every text box in
    the form carries a key Streamlit has never issued before, so nothing
    — server state or the browser's own copy of the form — has anything
    to restore into it. An emptied value with the *same* key is the
    version of this feature that passed its tests and shipped broken.
    """
    at = filled_form()

    keys = [w.key for w in at.text_input if w.label in {"Code", "Room"}]
    assert keys == ["add_thing.code#1", "add_thing.room#1"]
    assert box(at, "Note").key == "add_thing.note#1"
    assert box(at, "Untracked").key == "untracked_box"


def test_the_tick_box_and_the_picker_keep_their_setting():
    """The reason clear_on_submit=True is not used: it would reset these
    too, and the next row almost always wants the same ones."""
    at = filled_form()

    assert at.checkbox("add_thing.active").value is True
    assert at.selectbox("add_thing.grade").value == "Grade 12"


def test_a_text_box_not_built_by_text_field_is_left_alone():
    """Only boxes built by text_field carry a generation, so nothing else
    on the page can be blanked by a clear."""
    at = filled_form()

    assert box(at, "Untracked").value == "left alone"


def test_nothing_raises_on_the_clearing_run():
    at = filled_form()

    assert not at.exception


# --- What a failed add keeps ----------------------------------------------


def test_a_failed_save_keeps_what_was_typed():
    """A rejected code is the one moment retyping everything hurts most,
    and the row was not created — so the text has to survive."""
    at = filled_form(succeeds=False)

    assert box(at, "Code").value == "ORAL-COMM"
    assert box(at, "Note").value == "three terms"
    assert box(at, "Code").key == "add_thing.code#0", "the generation must not advance"


# --- The notification -----------------------------------------------------


def test_the_result_is_shown_both_in_place_and_as_a_toast():
    """The add forms sit below the list they add to, so the top of the
    page is scrolled off by the time you press Add. The toast is the copy
    that is actually visible; the inline one is the copy that stays put."""
    at = filled_form()

    assert [s.value for s in at.success] == ["Added ORAL-COMM."]
    assert [t.value for t in at.toast] == ["Added ORAL-COMM."]


def test_an_error_is_toasted_too():
    at = filled_form(succeeds=False)

    assert [e.value for e in at.error] == ["Couldn't save — that code is already used."]
    assert len(at.toast) == 1


def test_every_flash_kind_has_a_toast_icon():
    """st.toast takes an emoji, not a message kind. A kind with no icon
    still toasts, but silently loses the success/error distinction."""
    from app.admin_pages._helpers import _TOAST_ICONS

    for kind in _TOAST_ICONS:
        assert hasattr(st, kind), f"flash kind {kind} is not a Streamlit message function"
    assert set(_TOAST_ICONS) >= {"success", "error"}
