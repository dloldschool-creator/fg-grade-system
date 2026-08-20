"""The Subject Units page, driven through Streamlit's own runtime.

Units are the one piece of grading configuration that produces a *plausible*
wrong answer when it is wrong — a 3 typed where a 12 belongs shifts a
General Average by a couple of marks and reports nothing. So the page that
edits them is worth exercising rather than eyeballing.

`AppTest` runs the real Streamlit script, so widget wiring and reruns are
real; it has no browser, so anything about what a widget *displays* after a
rerun still needs one (see tests/test_add_form_reset.py's warning). Nothing
here depends on rendering — the assertions are about which rows the page
would write and which it would refuse.

`require_role` is patched out because the page is behind a login and these
tests have no session. That is the only thing faked.
"""

from decimal import Decimal

import pytest
from streamlit.testing.v1 import AppTest

from app.admin_pages import subject_units
from app.database import SessionLocal
from app.models.subjects import SubjectCategory

PAGE_SCRIPT = """
import streamlit as st
from types import SimpleNamespace
import app.admin_pages.subject_units as page

# The page is behind require_role; these tests have no session.
page.require_role = lambda *roles: SimpleNamespace(id=None, full_name="Test Admin")
page.render()
"""


@pytest.fixture
def app():
    at = AppTest.from_string(PAGE_SCRIPT, default_timeout=60)
    at.run()
    return at


# --- The page loads at all ------------------------------------------------


def test_the_page_renders_without_error(app):
    """A page that raises is a page nobody can use to fix the units that
    made it raise."""
    assert not app.exception, [str(e) for e in app.exception]
    assert "Subject Units" in [h.value for h in app.title]


def test_it_shows_the_averaging_method_actually_in_force(app):
    """Units are inert unless the grading policy is unit-weighted, so a page
    listing unit values without saying which rule applies would invite
    someone to conclude the values are being used when they are not."""
    metrics = [m.label for m in app.metric]
    assert metrics, "no averaging-method summary rendered"
    assert any("averaging" in label for label in metrics)
    values = [m.value for m in app.metric]
    assert all(v in ("Unit-weighted", "Flat average") for v in values), values


def test_all_three_tabs_are_reachable(app):
    """`st.tabs` resets to the first tab on every rerun, which is why the
    page uses `stateful_tabs`. If that regressed, editing anything but the
    first table would be impossible — the save would rerun and bounce you
    back."""
    assert app.radio, "no tab control rendered — stateful_tabs is missing"
    options = set(app.radio[0].options)
    assert {"Category defaults", "Per-subject overrides", "Combined areas"} <= options


# --- The refusals, which are the point ------------------------------------


def test_zero_units_is_refused_rather_than_written():
    """0 is a number someone will type meaning "don't count this subject".

    It would instead remove the subject from the denominator of every
    average with no trace — blank is how you say inherit, and there is no
    way to say weightless. Asserted against the real save path, not the
    widget.
    """
    session = SessionLocal()
    try:
        category = session.query(SubjectCategory).first()
        if category is None:
            pytest.skip("no subject categories in the database")
        before = category.units_per_term

        errors = []
        subject_units.st = _Recorder(errors)
        try:
            subject_units._save(
                session,
                _user(),
                [(category, {subject_units.UNITS_COLUMN: 0})],
                "subject_category",
                lambda c: c.code,
            )
        finally:
            subject_units.st = _real_streamlit()

        assert errors, "0 units was accepted silently"
        assert "blank" in errors[0].lower()
        session.rollback()
        assert category.units_per_term == before
    finally:
        session.rollback()
        session.close()


def test_resaving_an_untouched_table_writes_nothing():
    """Opening the page and pressing Save must not manufacture an audit
    entry per row, or the audit log stops being a record of decisions."""
    session = SessionLocal()
    try:
        categories = session.query(SubjectCategory).all()
        if not categories:
            pytest.skip("no subject categories in the database")
        rows = [
            {subject_units.UNITS_COLUMN: float(c.units_per_term)
             if c.units_per_term is not None else None}
            for c in categories
        ]
        messages = []
        subject_units.st = _Recorder(messages, capture="info")
        try:
            subject_units._save(
                session, _user(), zip(categories, rows), "subject_category",
                lambda c: c.code,
            )
        finally:
            subject_units.st = _real_streamlit()
        assert messages == ["Nothing changed."], messages
        assert not session.dirty
        assert not session.new
    finally:
        session.rollback()
        session.close()


# --- Normalisation the editor can hand back -------------------------------


@pytest.mark.parametrize("value", [None, float("nan"), "", "not a number"])
def test_a_blank_cell_means_inherit_not_zero(value):
    """An emptied numeric cell comes back as None or NaN depending on how
    the editor round-trips it. NaN is the dangerous one: it is a float, so a
    naive check treats it as a real value and writes it into a NUMERIC
    column."""
    assert subject_units._cell({"u": value}, "u") is None


def test_two_and_two_point_zero_are_not_an_edit():
    assert subject_units._changed(Decimal("2.00"), 2.0) is False
    assert subject_units._changed(Decimal("2.00"), 3.0) is True
    assert subject_units._changed(None, 2.0) is True
    assert subject_units._changed(Decimal("2.00"), None) is True


# --- Cost -----------------------------------------------------------------


def test_loading_the_page_does_not_scale_with_the_catalog():
    """369 offerings and 32 subjects must cost the same fixed handful of
    queries as three would — the database is ~85ms away and Streamlit reruns
    the whole script on every interaction."""
    from sqlalchemy import event

    from app.database import engine

    counter = {"n": 0}
    listener = lambda *a, **k: counter.__setitem__("n", counter["n"] + 1)  # noqa: E731
    session = SessionLocal()
    try:
        event.listen(engine, "before_cursor_execute", listener)
        try:
            context = subject_units._load(session)
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        assert counter["n"] <= 12, (
            f"{counter['n']} queries to load {len(context['subjects'])} subjects and "
            f"{len(context['offerings'])} offerings — something is querying per row"
        )
    finally:
        session.close()


# --- Test doubles ---------------------------------------------------------


def _user():
    from types import SimpleNamespace

    return SimpleNamespace(id=None)


def _real_streamlit():
    import streamlit

    return streamlit


class _Recorder:
    """Stands in for `st` so the save path's messages can be read back
    without a Streamlit script context."""

    def __init__(self, sink, capture="error"):
        self._sink = sink
        self._capture = capture

    def __getattr__(self, name):
        def call(*args, **kwargs):
            if name == self._capture and args:
                self._sink.append(str(args[0]))
            return None

        return call
