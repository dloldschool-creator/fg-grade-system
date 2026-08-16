"""An expander containing a live widget must survive its own rerun.

**`st.expander` has no memory.** Every rerun rebuilds it closed. And any
widget *outside* an `st.form` reruns the script the moment it changes —
so a picker, a tick box or a file uploader sitting directly inside an
expander slams its own panel shut, taking with it whatever it just
produced and the button underneath.

It shipped on Sections: choosing an adviser closed the panel on the
warning it had just raised, and the Save button went too. An audit then
found the same shape on five more pages. The worst were Learners and
Subject Catalog, where the entire import flow — preview, errors, confirm
— renders *inside* the expander, so uploading a file collapsed everything
and read as nothing having happened.

Nothing about it looks wrong in the source, which is why it is checked
structurally rather than left to review — the same reasoning as
`tests/test_expander_cost.py`, and the same reasoning that produced
`_helpers.stateful_tabs` for `st.tabs`.

The fix is always the same pair: `expanded=panel_is_open(id)` on the
expander, and `on_change=keep_panel_open, args=(id,)` on each widget
inside it that isn't in a form.
"""

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parent.parent / "app"

# Widgets whose value change reruns the script. Buttons are excluded:
# they are terminal actions that already end in an explicit st.rerun(),
# so a panel closing behind them is the intended outcome.
RERUNNING_WIDGETS = {
    "selectbox", "checkbox", "radio", "multiselect", "slider", "text_input",
    "number_input", "text_area", "toggle", "date_input", "time_input",
    "color_picker", "file_uploader", "data_editor", "select_slider", "pills",
}


def _is_call(node, attr):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
    )


def _with_context(node, attr):
    return isinstance(node, ast.With) and any(
        _is_call(item.context_expr, attr) for item in node.items
    )


def _expander_blocks(tree):
    for node in ast.walk(tree):
        if _with_context(node, "expander"):
            call = next(
                item.context_expr for item in node.items if _is_call(item.context_expr, "expander")
            )
            yield node, call


def _live_widgets(block):
    """Widget calls under `block` that are not inside a `with st.form`.

    A widget inside a form doesn't rerun until the form is submitted, so
    it cannot collapse the panel mid-edit and needs nothing.
    """
    inside_a_form = set()
    for node in ast.walk(block):
        if _with_context(node, "form"):
            inside_a_form |= {id(n) for n in ast.walk(node)}

    for node in ast.walk(block):
        if id(node) in inside_a_form:
            continue
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in RERUNNING_WIDGETS
        ):
            yield node


def _page_files():
    return sorted(APP.rglob("*.py"))


def _label(call):
    if call.args and isinstance(call.args[0], ast.Constant):
        return call.args[0].value
    return "?"


@pytest.mark.parametrize("path", _page_files(), ids=lambda p: p.name)
def test_an_expander_with_a_live_widget_declares_expanded(path):
    """Without `expanded=`, the panel rebuilds closed on the very rerun
    its own widget caused."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for block, call in _expander_blocks(tree):
        widgets = list(_live_widgets(block))
        if not widgets:
            continue
        assert any(k.arg == "expanded" for k in call.keywords), (
            f"{path.name}:{call.lineno} — expander holds "
            f"{', '.join('st.%s(%r)' % (w.func.attr, _label(w)) for w in widgets)} "
            "outside a form but takes no expanded=; it will collapse on change. "
            "Use expanded=panel_is_open(id)."
        )


@pytest.mark.parametrize("path", _page_files(), ids=lambda p: p.name)
def test_every_live_widget_in_an_expander_keeps_its_panel_open(path):
    """`expanded=` alone is not enough — something has to *set* the open
    panel, and that is each widget's `on_change`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for block, call in _expander_blocks(tree):
        for widget in _live_widgets(block):
            handlers = {k.arg for k in widget.keywords}
            assert "on_change" in handlers, (
                f"{path.name}:{widget.lineno} — st.{widget.func.attr}"
                f"({_label(widget)!r}) sits in an expander outside a form with no "
                "on_change; changing it collapses the panel. Use "
                "on_change=keep_panel_open, args=(id,)."
            )


def test_the_helper_pair_exists_and_agrees_on_where_it_stores_the_answer():
    """Two functions reading different keys would fail silently — the
    panel would simply never reopen, which looks like the original bug."""
    from app.admin_pages import _helpers

    _helpers.keep_panel_open("panel-1")
    assert _helpers.panel_is_open("panel-1")
    assert not _helpers.panel_is_open("panel-2")


def test_only_one_panel_is_held_open_at_a_time():
    """Holding every touched panel open would leave a 33-section list
    fully expanded. The one being typed in is the one that matters."""
    from app.admin_pages import _helpers

    _helpers.keep_panel_open("panel-1")
    _helpers.keep_panel_open("panel-2")
    assert _helpers.panel_is_open("panel-2")
    assert not _helpers.panel_is_open("panel-1")


# --- Through Streamlit's own runtime ---------------------------------------
#
# The checks above are structural: they prove the pair is wired up, not
# that it works. This session's whole lesson is that those are different
# things — a widget-clearing fix passed every server-side assertion here
# and still failed in a browser. So the mechanism itself is driven through
# the real runtime: the callback has to actually fire, and the expander
# has to actually come back open.

PANELS = """
import streamlit as st
from app.admin_pages._helpers import keep_panel_open, panel_is_open

for pid in ("panel-a", "panel-b"):
    with st.expander(pid, expanded=panel_is_open(pid)):
        st.selectbox("Pick", ["x", "y", "z"], key=f"sel_{pid}",
                     on_change=keep_panel_open, args=(pid,))
"""


def _panels():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(PANELS)
    app.run()
    return app


def test_every_panel_starts_closed():
    """A list of sections that opened itself would be unusable at 33 of
    them."""
    app = _panels()
    assert not app.exception
    assert [e.proto.expanded for e in app.expander] == [False, False]


def test_changing_a_widget_reopens_its_own_panel_and_only_that_one():
    """The bug, exactly: this rerun is the one that used to close the
    panel on whatever it had just produced."""
    app = _panels()
    app.selectbox[1].set_value("z").run()

    assert not app.exception
    assert app.session_state["_open_panel"] == "panel-b"
    assert [e.proto.expanded for e in app.expander] == [False, True], (
        "the panel holding the changed widget must come back open, and its "
        "neighbours must not"
    )
    # And the value really was applied — a panel that reopened while
    # losing the edit would be a worse bug than the one being fixed.
    assert app.selectbox[1].value == "z"


def test_the_open_panel_moves_with_the_user():
    app = _panels()
    app.selectbox[1].set_value("z").run()
    app.selectbox[0].set_value("y").run()
    assert [e.proto.expanded for e in app.expander] == [True, False]
