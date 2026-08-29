"""Overview → Insights, and the arithmetic it must not get wrong.

Two things are tested here that no amount of looking at the screen would
catch, because both produce a plausible wrong number rather than an
error:

1. **The query count must not scale with the roster.** ~32,000 grade rows
   have to be counted in Postgres. A version of this that loads them and
   counts in Python renders identically on a seeded three-learner section
   and takes a minute on the real one.
2. **A percentage is never averaged or summed.** That mistake has shipped
   twice in this codebase already (SF2, then SF4).
"""

import uuid
from decimal import Decimal

import pytest

from app import analytics_service
from app.analytics_service import (
    AtRiskRow,
    EncodingRow,
    SubjectGradeRow,
    advised_section_ids,
    at_risk_headline,
    at_risk_learners,
    distribution,
    encoding_progress,
    grade_bands,
    offering_progress,
    roll_up,
    subject_difficulty,
    subject_grade_stats,
    taught_offering_ids,
)
from app.database import SessionLocal
from app.models.organization import SchoolYear


@pytest.fixture
def session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def school_year(session):
    year = session.query(SchoolYear).order_by(SchoolYear.name.desc()).first()
    if year is None:
        pytest.skip("no school year in the database")
    return year


def _row(**overrides) -> EncodingRow:
    """An EncodingRow with everything but the counts stubbed out."""
    fields = dict(
        section_id=uuid.uuid4(),
        section_name="TEST",
        grade_level_id=uuid.uuid4(),
        grade_level_name="Grade 11",
        grade_level_order=1,
        track_id=uuid.uuid4(),
        track_name="Academic",
        strand_id=uuid.uuid4(),
        strand_name="STEM",
        term_id=uuid.uuid4(),
        term_name="Term 1",
        term_number=1,
        encoding_status="OPEN",
        submission_deadline=None,
        active_learners=40,
        offerings=9,
        placeholder_offerings=0,
        encoded=0,
    )
    fields.update(overrides)
    return EncodingRow(**fields)


# --- The arithmetic --------------------------------------------------------


def test_expected_is_learners_times_offerings():
    """Offerings are per section (§48), so every learner on the roll is
    graded on every one of them."""
    assert _row(active_learners=40, offerings=9).expected == 360


def test_a_section_with_nothing_offered_has_no_percentage():
    """Not 0% — 0% would sort an unconfigured section above one where
    teachers are genuinely late. Same reasoning as rule 2: absent is not
    zero."""
    assert _row(offerings=0).percent is None
    assert _row(active_learners=0).percent is None


def test_percent_is_encoded_over_expected():
    assert _row(active_learners=10, offerings=2, encoded=15).percent == 75.0


def test_missing_never_goes_negative():
    """Defensive: the encoded count is filtered to the same enrollment
    statuses as the expected count, so it should not overshoot — but a
    negative "still missing" on a dashboard is worse than a zero."""
    assert _row(active_learners=1, offerings=1, encoded=5).missing == 0


def test_roll_up_recomputes_the_percentage_from_the_totals():
    """**Not** the mean of the rows' percentages. A 5-learner section at
    100% and a 45-learner section at 0% is 10% of the work done, not
    50%."""
    small = _row(active_learners=5, offerings=1, encoded=5)
    large = _row(active_learners=45, offerings=1, encoded=0)

    encoded, expected, percent = roll_up([small, large])

    assert (encoded, expected) == (5, 50)
    assert percent == 10.0
    mean_of_percentages = (small.percent + large.percent) / 2
    assert percent != mean_of_percentages, "percentages were averaged"


def test_roll_up_of_nothing_is_not_zero_percent():
    assert roll_up([]) == (0, 0, None)


def test_roll_up_ignores_rows_that_expect_nothing():
    """A section with no offerings must not drag the total down — it
    contributes 0 to both halves of the fraction, not 0% to an average."""
    real = _row(active_learners=10, offerings=1, encoded=10)
    empty = _row(active_learners=10, offerings=0)
    assert roll_up([real, empty])[2] == 100.0


# --- Against the live database ---------------------------------------------


def test_the_cost_does_not_scale_with_the_roster(session, school_year):
    """One row per section per term, and a fixed number of round trips to
    build them all. The database is ~85ms away and Streamlit re-runs the
    whole script on every widget interaction, so this is per *click*."""
    from tests.test_query_cost import QueryCounter

    encoding_progress(session, school_year.id)  # warm any lazy metadata
    with QueryCounter() as counter:
        rows = encoding_progress(session, school_year.id)

    assert counter.count <= 8, f"{counter.count} queries for encoding progress"
    if not rows:
        pytest.skip("no sections or terms in this school year")


def test_every_section_term_combination_appears_once(session, school_year):
    """The page filters the returned rows rather than re-querying, so a
    missing combination is a silently absent section, not an error."""
    rows = encoding_progress(session, school_year.id)
    if not rows:
        pytest.skip("no sections or terms in this school year")
    keys = [(r.section_id, r.term_id) for r in rows]
    assert len(keys) == len(set(keys))
    assert len(keys) == len({r.section_id for r in rows}) * len({r.term_id for r in rows})


def test_rows_arrive_in_display_order(session, school_year):
    """The page renders them as they come — sorting in the view instead
    would put the two apart and let them drift."""
    rows = encoding_progress(session, school_year.id)
    if len(rows) < 2:
        pytest.skip("needs at least two rows")
    ordered = [
        (r.grade_level_order, r.track_name, r.strand_name, r.section_name, r.term_number)
        for r in rows
    ]
    assert ordered == sorted(ordered)


def test_no_section_reports_more_than_complete(session, school_year):
    """The numerator and the denominator have to describe the same
    population. They fall out of step the moment one of them forgets to
    filter on enrollment status — a learner who transferred out after
    being graded leaves the denominator and keeps the numerator, and the
    section reports 104%."""
    rows = encoding_progress(session, school_year.id)
    if not rows:
        pytest.skip("no sections or terms in this school year")
    for row in rows:
        assert row.encoded <= row.expected, (
            f"{row.section_name} / {row.term_name}: {row.encoded} encoded of "
            f"{row.expected} expected"
        )


def test_the_active_status_set_matches_the_gradebook_roster():
    """This page's headcount and the roster a teacher is looking at while
    reading it have to be the same set, or the percentage is measured
    against a class that does not exist."""
    from app.admin_pages.gradebook import ROSTER_STATUSES

    assert set(analytics_service.ACTIVE_ENROLLMENT_STATUSES) == set(ROSTER_STATUSES)


# --- Grade bands -----------------------------------------------------------


def test_the_familiar_bands_come_out_of_the_default_passing_mark():
    assert [b.label for b in grade_bands(75.0)] == [
        "Below 75",
        "75–79",
        "80–84",
        "85–89",
        "90 and above",
    ]


def test_the_bands_move_with_the_passing_mark():
    """The threshold is a versioned policy value, not a constant. A band
    set hardcoded to 75 would keep calling a 78 "passing" in a year that
    was graded on 80."""
    assert [b.label for b in grade_bands(80.0)][0] == "Below 80"
    assert grade_bands(80.0)[1].lower == 80.0


def test_the_passing_mark_itself_is_not_below_passing():
    """The classic off-by-one. 75 passes."""
    bands = grade_bands(75.0)
    below, first_passing = bands[0], bands[1]
    assert below.upper == 75.0, "the below-passing band must stop short of 75"
    assert first_passing.lower == 75.0


# --- Distribution and difficulty, as pure functions -------------------------


def _subject_row(**overrides):
    fields = dict(
        section_id=uuid.uuid4(),
        section_name="BEZOS",
        grade_level_id=uuid.uuid4(),
        grade_level_name="Grade 11",
        grade_level_order=1,
        track_name="Academic",
        strand_id=uuid.uuid4(),
        strand_name="STEM",
        term_id=uuid.uuid4(),
        term_name="Term 1",
        term_number=1,
        subject_id=uuid.uuid4(),
        subject_name="General Mathematics",
        subject_code="GENMATH",
        band_counts=(0, 0, 0, 0, 0),
        graded=0,
        total=0.0,
        lowest=0.0,
        highest=0.0,
    )
    fields.update(overrides)
    return SubjectGradeRow(**fields)


def test_distribution_adds_the_bands_across_rows():
    bands = grade_bands(75.0)
    rows = [
        _subject_row(band_counts=(1, 2, 3, 4, 5), graded=15),
        _subject_row(band_counts=(1, 0, 0, 0, 1), graded=2),
    ]
    assert [count for _band, count in distribution(rows, bands)] == [2, 2, 3, 4, 6]


def test_below_passing_reads_the_first_band():
    assert _subject_row(band_counts=(4, 1, 0, 0, 0), graded=5).below_passing == 4


def test_subject_difficulty_recomputes_the_mean_from_the_totals():
    """**Not** the mean of the sections' means. A 45-learner section at 70
    and a 5-learner section at 90 average 72, not 80."""
    subject_id = uuid.uuid4()
    big = _subject_row(subject_id=subject_id, graded=45, total=45 * 70.0, lowest=70.0, highest=70.0)
    small = _subject_row(subject_id=subject_id, graded=5, total=5 * 90.0, lowest=90.0, highest=90.0)

    entry = subject_difficulty([big, small])[0]

    assert entry.graded == 50
    assert entry.average == pytest.approx(72.0)
    assert entry.average != pytest.approx((70.0 + 90.0) / 2), "the averages were averaged"


def test_subject_difficulty_ranks_the_most_failures_first():
    """Ranking on the average alone hides the case that matters most: a
    subject sitting at a comfortable mean with a tail of failures."""
    gentle = uuid.uuid4()
    harsh = uuid.uuid4()
    rows = [
        _subject_row(
            subject_id=gentle, subject_name="Gentle", graded=10,
            band_counts=(0, 10, 0, 0, 0), total=780.0, lowest=78.0, highest=78.0,
        ),
        _subject_row(
            subject_id=harsh, subject_name="Harsh", graded=10,
            band_counts=(4, 0, 0, 0, 6), total=860.0, lowest=60.0, highest=95.0,
        ),
    ]
    ranked = subject_difficulty(rows)
    assert ranked[0].subject_name == "Harsh"
    assert ranked[0].percent_below_passing == 40.0
    assert ranked[0].average > ranked[1].average, (
        "the harder subject here has the *higher* mean — which is the "
        "reason the ranking is not on the mean"
    )


def test_a_subject_nobody_has_been_graded_in_has_no_percentage():
    assert _subject_row(graded=0).average is None
    assert subject_difficulty([]) == []


# --- The bucketing SQL, against the real database --------------------------


def test_grades_land_in_the_right_bands(session, school_year):
    """The one thing no pure test can reach: that Postgres buckets the
    grades the way the labels claim.

    Writes a handful of grade rows, reads them back through the aggregate
    and **rolls back** — the fixture never commits, so nothing here
    reaches the school's data. It is worth the trouble because
    `term_grades` is still empty in this database, which means the
    grouping would otherwise ship having never run against a single row.
    """
    from app.models.grades import TermGrade
    from app.models.learners import Enrollment
    from app.models.subjects import SectionSubjectOffering

    offering = (
        session.query(SectionSubjectOffering)
        .filter_by(school_year_id=school_year.id)
        .first()
    )
    if offering is None:
        pytest.skip("no offerings in this school year")
    roster = (
        session.query(Enrollment)
        .filter_by(school_year_id=school_year.id, section_id=offering.section_id)
        .limit(6)
        .all()
    )
    if len(roster) < 6:
        pytest.skip("needs six learners in the offering's section")

    # One grade per band, plus a second below-passing one, plus the
    # passing mark exactly — the boundary that is easiest to get wrong.
    grades = ["70", "74", "75", "83", "88", "97"]
    for enrollment, grade in zip(roster, grades):
        session.add(
            TermGrade(
                enrollment_id=enrollment.id,
                section_subject_offering_id=offering.id,
                term_id=offering.term_id,
                official_grade=Decimal(grade),
            )
        )
    session.flush()  # visible to this transaction only; never committed

    stats = subject_grade_stats(session, school_year.id)
    row = next(
        r
        for r in stats.rows
        if r.section_id == offering.section_id and r.term_id == offering.term_id
    )

    assert row.band_counts == (2, 1, 1, 1, 1)
    assert row.graded == 6
    assert row.below_passing == 2, "75 must not count as below passing"
    assert row.average == pytest.approx(487 / 6)
    assert (row.lowest, row.highest) == (70.0, 97.0)


def test_the_stats_cost_does_not_scale_with_the_roster(session, school_year):
    from tests.test_query_cost import QueryCounter

    subject_grade_stats(session, school_year.id)  # warm
    with QueryCounter() as counter:
        subject_grade_stats(session, school_year.id)
    assert counter.count <= 8, f"{counter.count} queries for the grade stats"


# --- Adviser scoping -------------------------------------------------------
#
# The rule these guard: `section_ids=None` means the whole school, and an
# **empty tuple means a viewer entitled to nothing**. Treating the two
# alike — the natural thing to write, since both are falsy — shows every
# learner in the school to an adviser holding no section.


def _any_advised_section(session, school_year):
    from app.models.academic_structure import Section

    section = (
        session.query(Section)
        .filter(
            Section.school_year_id == school_year.id,
            Section.adviser_user_id.isnot(None),
        )
        .first()
    )
    if section is None:
        pytest.skip("no section with an adviser")
    return section


def test_an_empty_scope_returns_nothing_not_everything(session, school_year):
    """The fail-closed case, on every metric that takes a scope."""
    assert encoding_progress(session, school_year.id, ()) == []
    assert subject_grade_stats(session, school_year.id, ()).rows == ()
    assert at_risk_learners(session, school_year.id, ()).rows == ()
    assert offering_progress(session, school_year.id, ()) == []


def test_none_means_the_whole_school(session, school_year):
    """And is not accidentally equivalent to the empty scope."""
    everything = encoding_progress(session, school_year.id, None)
    if not everything:
        pytest.skip("no sections in this school year")
    assert len(everything) > len(encoding_progress(session, school_year.id, ()))


def test_an_adviser_sees_only_the_sections_they_advise(session, school_year):
    section = _any_advised_section(session, school_year)
    adviser = str(section.adviser_user_id)

    ids = advised_section_ids(session, school_year.id, adviser)
    assert section.id in ids

    rows = encoding_progress(session, school_year.id, ids)
    assert rows, "the adviser's own section should be present"
    assert {r.section_id for r in rows} <= set(ids)

    everything = encoding_progress(session, school_year.id, None)
    assert len(rows) < len(everything), "scoping did not narrow anything"


def test_advised_sections_are_matched_in_sql_not_python(session, school_year):
    """`AuthUser.id` is a `str` and `sections.adviser_user_id` is a UUID.
    Postgres coerces; Python does not. Passing the string form is what
    the page actually does, so it is what the test does."""
    section = _any_advised_section(session, school_year)

    as_text = advised_section_ids(session, school_year.id, str(section.adviser_user_id))
    as_uuid = advised_section_ids(session, school_year.id, section.adviser_user_id)

    assert section.id in as_text, "a str adviser id must match"
    assert as_text == as_uuid


def test_an_adviser_of_nothing_gets_an_empty_tuple(session, school_year):
    import uuid as _uuid

    assert advised_section_ids(session, school_year.id, str(_uuid.uuid4())) == ()
    assert advised_section_ids(session, school_year.id, None) == ()


def test_at_risk_never_loads_a_learner_outside_the_scope(session, school_year):
    """Scoped in the query rather than filtered afterwards — another
    adviser's learner must not be loaded even to be discarded."""
    section = _any_advised_section(session, school_year)
    ids = advised_section_ids(session, school_year.id, str(section.adviser_user_id))
    report = at_risk_learners(session, school_year.id, ids)
    assert {r.section_id for r in report.rows} <= set(ids)


# --- Subject teacher scoping -----------------------------------------------
#
# A subject teacher is scoped by **offering**, not by section, and that
# is a privacy boundary rather than a convenience: their classes sit
# inside sections whose other subjects belong to colleagues.


def _any_teacher(session, school_year):
    from app.models.subjects import SectionSubjectOffering, TeacherAssignment

    row = (
        session.query(TeacherAssignment)
        .join(
            SectionSubjectOffering,
            TeacherAssignment.section_subject_offering_id == SectionSubjectOffering.id,
        )
        .filter(
            TeacherAssignment.is_active.is_(True),
            SectionSubjectOffering.school_year_id == school_year.id,
        )
        .first()
    )
    if row is None:
        pytest.skip("no active teaching assignment")
    return str(row.teacher_user_id)


def test_a_teacher_gets_only_the_classes_they_hold(session, school_year):
    from app.models.subjects import SectionSubjectOffering

    teacher = _any_teacher(session, school_year)
    ids = taught_offering_ids(session, school_year.id, teacher)
    assert ids, "the teacher should hold at least one offering"

    everything = {
        o.id
        for o in session.query(SectionSubjectOffering)
        .filter_by(school_year_id=school_year.id)
        .all()
    }
    assert set(ids) < everything, "the teacher scope did not narrow anything"


def test_a_teacher_does_not_get_the_rest_of_their_sections_subjects(session, school_year):
    """The leak this scope exists to prevent.

    Scoping a subject teacher by the *sections* their classes are in
    would hand them every other subject in those sections — their
    colleagues' grades. The offering scope must be strictly smaller than
    the section scope wherever a section runs more than one subject.
    """
    from app.models.subjects import SectionSubjectOffering

    teacher = _any_teacher(session, school_year)
    ids = taught_offering_ids(session, school_year.id, teacher)
    rows = offering_progress(session, school_year.id, offering_ids=ids)
    if not rows:
        pytest.skip("no offerings resolved")

    their_sections = {r.section_id for r in rows}
    everything_in_those_sections = {
        o.id
        for o in session.query(SectionSubjectOffering)
        .filter(
            SectionSubjectOffering.school_year_id == school_year.id,
            SectionSubjectOffering.section_id.in_(their_sections),
        )
        .all()
    }
    if len(everything_in_those_sections) <= len(ids):
        pytest.skip("this teacher happens to hold every offering in their sections")
    assert set(ids) < everything_in_those_sections
    assert {r.subject_id for r in rows} != {
        o.subject_id
        for o in session.query(SectionSubjectOffering)
        .filter(SectionSubjectOffering.section_id.in_(their_sections))
        .all()
    }


def test_an_unknown_teacher_holds_nothing(session, school_year):
    import uuid as _uuid

    assert taught_offering_ids(session, school_year.id, str(_uuid.uuid4())) == ()
    assert taught_offering_ids(session, school_year.id, None) == ()


def test_offering_progress_needs_a_scope_and_refuses_to_go_school_wide(session, school_year):
    """Neither scope given must return nothing, not everything. The
    school-wide path is the section-level metric, not this one."""
    assert offering_progress(session, school_year.id) == []
    assert offering_progress(session, school_year.id, None, None) == []
    assert offering_progress(session, school_year.id, offering_ids=()) == []


def test_grade_stats_takes_the_offering_scope_too(session, school_year):
    """Otherwise a teacher's distribution would be built from every
    subject in their sections, colleagues' grades included."""
    assert subject_grade_stats(session, school_year.id, offering_ids=()).rows == ()

    teacher = _any_teacher(session, school_year)
    ids = taught_offering_ids(session, school_year.id, teacher)
    scoped = subject_grade_stats(session, school_year.id, offering_ids=ids)
    assert all(r.subject_id is not None for r in scoped.rows)


def test_a_teachers_view_costs_the_same_at_thirty_classes(session, school_year):
    """One teacher here holds 30 offerings. The query count must not
    follow."""
    from tests.test_query_cost import QueryCounter

    teacher = _any_teacher(session, school_year)
    ids = taught_offering_ids(session, school_year.id, teacher)
    offering_progress(session, school_year.id, offering_ids=ids)  # warm
    with QueryCounter() as counter:
        offering_progress(session, school_year.id, offering_ids=ids)
    assert counter.count <= 8, f"{counter.count} queries for a teacher's classes"


def test_submitted_never_exceeds_encoded(session, school_year):
    """Submitting is a step past encoding (rule 7), so the submitted
    count is a subset. More submitted than encoded would mean the two
    counts were measured over different populations."""
    teacher = _any_teacher(session, school_year)
    ids = taught_offering_ids(session, school_year.id, teacher)
    for row in offering_progress(session, school_year.id, offering_ids=ids):
        assert row.submitted <= row.encoded


# --- The per-subject drill-down --------------------------------------------


def test_offering_progress_is_one_row_per_subject_and_term(session, school_year):
    section = _any_advised_section(session, school_year)
    rows = offering_progress(session, school_year.id, (section.id,))
    if not rows:
        pytest.skip("that section has no offerings")
    keys = [(r.subject_id, r.term_id) for r in rows]
    assert len(keys) == len(set(keys))
    assert all(r.section_id == section.id for r in rows)


def test_offering_progress_puts_the_least_done_first(session, school_year):
    section = _any_advised_section(session, school_year)
    rows = offering_progress(session, school_year.id, (section.id,))
    if len(rows) < 2:
        pytest.skip("needs at least two offerings")
    percents = [r.percent if r.percent is not None else 1e9 for r in rows]
    assert percents == sorted(percents)


def test_offering_progress_expects_one_grade_per_learner(session, school_year):
    """Per offering, every learner on the roll owes exactly one grade —
    unlike the section-level metric, where expected is multiplied by the
    number of subjects."""
    section = _any_advised_section(session, school_year)
    rows = offering_progress(session, school_year.id, (section.id,))
    if not rows:
        pytest.skip("that section has no offerings")
    for row in rows:
        assert row.expected == row.active_learners
        assert row.encoded <= row.expected


def test_the_drill_down_cost_is_flat(session, school_year):
    from tests.test_query_cost import QueryCounter

    section = _any_advised_section(session, school_year)
    offering_progress(session, school_year.id, (section.id,))  # warm
    with QueryCounter() as counter:
        offering_progress(session, school_year.id, (section.id,))
    assert counter.count <= 8, f"{counter.count} queries for the drill-down"


# --- Learners at risk ------------------------------------------------------


def _at_risk_row(**overrides):
    fields = dict(
        enrollment_id=uuid.uuid4(),
        learner_name="DELA CRUZ, JUAN",
        section_id=uuid.uuid4(),
        section_name="BEZOS",
        grade_level_id=uuid.uuid4(),
        grade_level_name="Grade 11",
        grade_level_order=1,
        strand_id=uuid.uuid4(),
        strand_name="STEM",
        term_id=uuid.uuid4(),
        term_name="Term 1",
        term_number=1,
        term_average=70.0,
        lowest_grade=65.0,
        failed_subjects=1,
        complete=True,
    )
    fields.update(overrides)
    return AtRiskRow(**fields)


def test_the_headline_counts_learners_and_flags_separately():
    """A learner failing all three terms is three rows and one learner.
    Reporting the row count as a headcount overstates the problem by
    exactly the amount the school would most want to get right."""
    enrollment = uuid.uuid4()
    rows = [
        _at_risk_row(enrollment_id=enrollment, term_number=1),
        _at_risk_row(enrollment_id=enrollment, term_number=2),
        _at_risk_row(enrollment_id=enrollment, term_number=3),
        _at_risk_row(),
    ]
    assert at_risk_headline(rows) == (2, 4)


def test_a_term_still_being_encoded_is_marked_provisional():
    """The average of two of nine subjects is not a verdict."""
    assert _at_risk_row(complete=False).provisional
    assert not _at_risk_row(complete=True).provisional


def test_at_risk_rows_come_back_worst_first(session, school_year):
    report = at_risk_learners(session, school_year.id)
    ordered = [
        (-r.failed_subjects, r.term_average if r.term_average is not None else 1e9)
        for r in report.rows
    ]
    assert ordered == sorted(ordered)


def test_the_at_risk_query_is_flat(session, school_year):
    from tests.test_query_cost import QueryCounter

    at_risk_learners(session, school_year.id)  # warm
    with QueryCounter() as counter:
        at_risk_learners(session, school_year.id)
    assert counter.count <= 10, f"{counter.count} queries for the at-risk list"


def test_who_gets_flagged_and_who_does_not(session, school_year):
    """The whole rule, against the real query.

    Writes five term summaries covering each branch and **rolls back** —
    the fixture never commits. Note this builds the summary rows directly
    rather than calling `recompute_enrollment_grades`, which commits and
    would leave the school's data changed.
    """
    from app.models.enums import CompletionStatus
    from app.models.grades import TermGradeSummary
    from app.models.learners import Enrollment
    from app.models.organization import Term

    term = (
        session.query(Term)
        .filter_by(school_year_id=school_year.id)
        .order_by(Term.term_number)
        .first()
    )
    if term is None:
        pytest.skip("no terms in this school year")
    already = {
        s.enrollment_id
        for s in session.query(TermGradeSummary).filter_by(term_id=term.id).all()
    }
    roster = [
        e
        for e in session.query(Enrollment)
        .filter_by(school_year_id=school_year.id)
        .limit(80)
        .all()
        if e.id not in already
    ][:5]
    if len(roster) < 5:
        pytest.skip("needs five learners without a summary for this term")

    before = len(at_risk_learners(session, school_year.id).rows)

    cases = [
        # (average, lowest, failed count, completion)  -> flagged?
        (Decimal("72"), Decimal("55"), 3, CompletionStatus.COMPLETE),      # yes
        (Decimal("81"), Decimal("70"), 1, CompletionStatus.INCOMPLETE),    # yes
        (Decimal("74"), Decimal("74"), 0, CompletionStatus.COMPLETE),      # yes
        (Decimal("88"), Decimal("83"), 0, CompletionStatus.COMPLETE),      # no
        (None, None, 0, CompletionStatus.INCOMPLETE),                      # no
    ]
    for enrollment, (average, lowest, failed, completion) in zip(roster, cases):
        session.add(
            TermGradeSummary(
                enrollment_id=enrollment.id,
                school_year_id=school_year.id,
                term_id=term.id,
                term_average=average,
                lowest_term_grade=lowest,
                failed_subject_count=failed,
                completion_status=completion,
            )
        )
    session.flush()  # visible to this transaction only; never committed

    report = at_risk_learners(session, school_year.id)
    added = {r.enrollment_id: r for r in report.rows if r.enrollment_id in {e.id for e in roster}}

    assert len(report.rows) - before == 3, "expected exactly three of the five to flag"
    assert roster[0].id in added, "three failing subjects must flag"
    assert roster[1].id in added, "one failing subject must flag"
    assert roster[2].id in added, "a term average below the passing mark must flag"
    assert roster[3].id not in added, "a passing learner must not flag"
    assert roster[4].id not in added, (
        "a learner nobody has graded is not a learner who is failing — "
        "a NULL average must never read as a low one"
    )
    assert added[roster[1].id].provisional, "an incomplete term must be marked provisional"
    assert added[roster[0].id].failed_subjects == 3


def test_an_unencoded_year_reports_no_grades_rather_than_zeroes(session, school_year):
    """What this school actually sees today. A subject nobody has graded
    must be absent, not a bar sitting at zero that reads as a class who
    all failed."""
    stats = subject_grade_stats(session, school_year.id)
    if stats.any_grades:
        pytest.skip("this school year has encoded grades")
    assert stats.rows == ()
    assert [count for _band, count in distribution(stats.rows, stats.bands)] == [0] * 5
