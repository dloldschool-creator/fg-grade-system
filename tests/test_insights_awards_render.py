"""The award-eligibility section of Insights, rendered for real.

Every other metric on that page is verified through
`analytics_service` and then trusted to display; the layout itself has
never been viewed signed in, and the award section has the most
conditional layout on the page — a Term column that only exists for a
per-term policy, a tier table that only appears for a ladder, three
warnings that each appear only when true, and a headline whose labels
change with how many terms are in view.

None of that can be reached without an account holding the right role,
so it is driven here through Streamlit's own runtime instead, against
constructed reports. `AppTest` has no browser and cannot tell you what a
widget *looks* like — see the note at the top of
`tests/test_add_form_reset.py` — but it does run the real script, which
is what catches a bad DataFrame or a metric built from a None.
"""

import uuid

import pytest
from streamlit.testing.v1 import AppTest

from app.analytics_service import (
    AwardEligibilityReport,
    AwardLearnerRow,
    AwardPolicyOption,
    AwardSectionRow,
)

SCRIPT = """
import streamlit as st
from app.admin_pages.insights import _render_awards

_render_awards(
    st.session_state["report"],
    st.session_state["rows"],
    st.session_state["eligible"],
)
"""


def _policy(per_term=False, tiered=False, requires_complete_record=True):
    return AwardPolicyOption(
        version_id=uuid.uuid4(),
        policy_name="Legacy Tiered Honors" if per_term else "Academic Excellence Award",
        version_number=1,
        scope="TERM" if per_term else "ANNUAL",
        status="ACTIVE",
        tiered=tiered,
        requires_complete_record=requires_complete_record,
    )


def _section(term=None, **counts):
    fields = dict(
        learners=40,
        computed=0,
        eligible=0,
        not_eligible=0,
        overridden=0,
        stale=0,
        incomplete_records=0,
    )
    fields.update(counts)
    term_id, term_name, term_number = term or (None, "", 0)
    return AwardSectionRow(
        section_id=uuid.uuid4(),
        section_name="BEZOS",
        grade_level_id=uuid.uuid4(),
        grade_level_name="Grade 11",
        strand_id=uuid.uuid4(),
        strand_name="Business Enterprise",
        term_id=term_id,
        term_name=term_name,
        term_number=term_number,
        **fields,
    )


def _learner(name="DELA CRUZ, Juan", average=95.0, term=None, **overrides):
    term_id, term_name, _number = term or (None, "", 0)
    fields = dict(
        award_name="With Honors",
        is_override=False,
        stale=False,
    )
    fields.update(overrides)
    return AwardLearnerRow(
        enrollment_id=uuid.uuid4(),
        learner_name=name,
        section_id=uuid.uuid4(),
        section_name="BEZOS",
        grade_level_name="Grade 11",
        term_id=term_id,
        term_name=term_name,
        average=average,
        **fields,
    )


def _run(report, rows, eligible):
    at = AppTest.from_string(SCRIPT, default_timeout=30)
    at.session_state["report"] = report
    at.session_state["rows"] = rows
    at.session_state["eligible"] = eligible
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    return at


def _text(at) -> str:
    """Everything the section wrote, as one blob."""
    parts = []
    for block in (at.info, at.warning, at.success, at.markdown, at.caption):
        parts.extend(element.value for element in block)
    for metric in at.metric:
        parts.append(f"{metric.label} {metric.value}")
    return "\n".join(parts)


def test_a_roster_nobody_has_judged_says_so_and_stops():
    """The state the school is actually in, and the one worth getting
    right: no table of zeroes, and no claim that anybody is ineligible."""
    policy = _policy()
    rows = [_section(learners=40)]
    at = _run(AwardEligibilityReport(policy, tuple(rows), ()), rows, [])

    text = _text(at)
    assert "has not been computed" in text
    assert "Nobody is ineligible" in text
    # A share of nothing is a dash, never 0%.
    share = next(m for m in at.metric if m.label == "Of those judged")
    assert share.value == "—"
    assert not at.dataframe, "nothing to tabulate until something is judged"


def test_an_annual_policy_names_its_eligible_learners():
    policy = _policy()
    rows = [_section(learners=40, computed=40, eligible=2, not_eligible=38)]
    eligible = [_learner("SANTOS, Ana", 97.0), _learner("REYES, Ben", 95.0)]
    at = _run(AwardEligibilityReport(policy, tuple(rows), tuple(eligible)), rows, eligible)

    text = _text(at)
    assert "2 eligible learner(s)" in text
    assert "General Average" in "".join(str(f.value.columns.tolist()) for f in at.dataframe)
    assert next(m for m in at.metric if m.label == "Eligible").value == "2"
    assert next(m for m in at.metric if m.label == "Of those judged").value == "5%"


def test_a_stale_result_is_flagged_and_an_override_is_not_folded_in():
    policy = _policy()
    rows = [_section(learners=40, computed=40, eligible=2, overridden=1, stale=1)]
    eligible = [
        _learner("SANTOS, Ana", 97.0, stale=True),
        _learner("REYES, Ben", 74.0, is_override=True),
    ]
    at = _run(AwardEligibilityReport(policy, tuple(rows), tuple(eligible)), rows, eligible)

    text = _text(at)
    assert "computed before" in text, "a stale result must warn"
    assert "set by hand" in text, "an override must be called out separately"
    assert any("Recompute" in str(f.value.values) for f in at.dataframe)


def test_a_term_policy_counts_learner_terms_and_says_which():
    """Three terms of one learner is three awards and one person.
    Reporting only the first overstates the honour roll by exactly the
    amount the school would most want right."""
    policy = _policy(per_term=True, tiered=True)
    terms = [(uuid.uuid4(), f"Term {n}", n) for n in (1, 2, 3)]
    rows = [
        _section(term=term, learners=40, computed=40, eligible=1, not_eligible=39)
        for term in terms
    ]
    one_learner = uuid.uuid4()
    # The same person, three times — one enrollment id across three terms.
    eligible = [
        AwardLearnerRow(
            enrollment_id=one_learner,
            learner_name="SANTOS, Ana",
            section_id=rows[0].section_id,
            section_name="BEZOS",
            grade_level_name="Grade 11",
            term_id=term_id,
            term_name=term_name,
            award_name="With Honors",
            average=95.0,
            is_override=False,
            stale=False,
        )
        for term_id, term_name, _number in terms
    ]
    at = _run(AwardEligibilityReport(policy, tuple(rows), tuple(eligible)), rows, eligible)

    text = _text(at)
    assert "1 eligible learner(s), 3 award(s)" in text
    assert "counts once as a learner and once per term" in text
    # The roster metric must not read 120 learners for a 40-learner section.
    judged = next(m for m in at.metric if m.label == "Judged")
    assert judged.value == "100%"
    assert "Term" in "".join(str(f.value.columns.tolist()) for f in at.dataframe)


def test_an_annual_policy_shows_no_term_column():
    policy = _policy()
    rows = [_section(learners=40, computed=40, eligible=1, not_eligible=39)]
    eligible = [_learner("SANTOS, Ana", 97.0)]
    at = _run(AwardEligibilityReport(policy, tuple(rows), tuple(eligible)), rows, eligible)

    columns = "".join(str(f.value.columns.tolist()) for f in at.dataframe)
    assert "'Term'" not in columns, "an annual award has no term dimension"


def test_the_completeness_note_only_appears_when_the_policy_asks_for_it():
    rows = [_section(learners=40, computed=40, not_eligible=40, incomplete_records=12)]
    demanding = _run(
        AwardEligibilityReport(_policy(), tuple(rows), ()), rows, []
    )
    assert "12 of" in _text(demanding)

    relaxed = _run(
        AwardEligibilityReport(
            _policy(requires_complete_record=False), tuple(rows), ()
        ),
        rows,
        [],
    )
    assert "12 of" not in _text(relaxed)


def test_nothing_in_view_renders_nothing_rather_than_raising():
    policy = _policy()
    at = _run(AwardEligibilityReport(policy, (), ()), [], [])
    assert "No sections in view" in _text(at)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
