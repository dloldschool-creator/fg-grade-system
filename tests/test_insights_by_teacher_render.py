"""The "Encoding progress by teacher" section of Insights, rendered for real.

Same reasoning as `tests/test_insights_awards_render.py`: this section has
conditional layout (an empty state, an "unassigned" pseudo-teacher, a
warning that only appears when something is actually unassigned) that
can't be reached without a school-wide account, so it is driven through
Streamlit's own runtime against constructed rows instead.
"""

import uuid

import pytest
from streamlit.testing.v1 import AppTest

from app.analytics_service import OfferingProgressRow

SCRIPT = """
import streamlit as st
from app.admin_pages.insights import _render_by_teacher

_render_by_teacher(st.session_state["rows"])
"""


def _row(section, subject, teacher, encoded, expected):
    return OfferingProgressRow(
        section_id=uuid.uuid4(),
        section_name=section,
        subject_id=uuid.uuid4(),
        subject_name=subject,
        subject_code=subject[:6].upper(),
        term_id=uuid.uuid4(),
        term_name="Term 1",
        term_number=1,
        teacher_name=teacher,
        display_order=0,
        active_learners=expected,
        encoded=encoded,
        submitted=0,
        term_encoding_status="OPEN",
        submission_deadline=None,
    )


def _run(rows):
    at = AppTest.from_string(SCRIPT, default_timeout=30)
    at.session_state["rows"] = rows
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    return at


def test_a_teacher_across_several_sections_is_one_row_not_several():
    """The whole point of grouping by teacher: a teacher holding classes in
    three sections must appear once, with the totals summed, not three
    times the way the by-section view would show them."""
    rows = [
        _row("GATES", "Effective Communication", "JEFFREY M. PINEDA", 0, 37),
        _row("MARSHALL", "Effective Communication", "JEFFREY M. PINEDA", 26, 28),
        _row("MISCHER", "Effective Communication", "JEFFREY M. PINEDA", 27, 28),
    ]
    at = _run(rows)

    summary = at.dataframe[0].value
    assert len(summary) == 1
    assert summary.iloc[0]["Teacher"] == "JEFFREY M. PINEDA"
    assert summary.iloc[0]["Classes"] == 3
    assert summary.iloc[0]["Missing"] == 37 + 2 + 1


def test_sorted_by_missing_descending():
    rows = [
        _row("BABBAGE", "General Science", "A TEACHER", 36, 36),  # complete
        _row("GATES", "General Science", "B TEACHER", 0, 37),
        _row("BEZOS", "General Science", "C TEACHER", 5, 35),
    ]
    at = _run(rows)

    summary = at.dataframe[0].value
    assert summary["Teacher"].tolist() == ["B TEACHER", "C TEACHER", "A TEACHER"]
    assert summary["Missing"].tolist() == [37, 30, 0]


def test_a_fully_encoded_teacher_has_no_detail_row():
    """A teacher with nothing missing must not appear in the "still needs
    grades" detail table, even though they still appear in the summary."""
    rows = [_row("BABBAGE", "General Science", "A TEACHER", 36, 36)]
    at = _run(rows)

    assert len(at.dataframe) == 1, "no detail table when nothing is missing"
    assert not at.markdown, "the detail header must not print with nothing under it"


def test_an_unassigned_offering_is_grouped_and_flagged_not_dropped():
    rows = [_row("COWIE", "Events Management Services NC III", "", 27, 30)]
    at = _run(rows)

    summary = at.dataframe[0].value
    assert summary.iloc[0]["Teacher"] == "Not assigned"
    text = "\n".join(w.value for w in at.warning)
    assert "no teacher assigned" in text


def test_nothing_in_view_renders_nothing_rather_than_raising():
    at = _run([])
    assert not at.dataframe
    assert "No subjects offered" in "\n".join(c.value for c in at.caption)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
