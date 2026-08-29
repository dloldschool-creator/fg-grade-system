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
    ACTIVE_ENROLLMENT_STATUSES,
    AtRiskRow,
    AwardLearnerRow,
    AwardSectionRow,
    EncodingRow,
    SubjectGradeRow,
    advised_section_ids,
    annual_risk,
    at_risk_headline,
    attendance_headline,
    attendance_risk,
    at_risk_learners,
    award_eligibility,
    award_headline,
    award_policy_options,
    award_tiers,
    distribution,
    encoding_progress,
    grade_bands,
    offering_progress,
    roll_up,
    subject_difficulty,
    subject_grade_stats,
    subject_learners_at_risk,
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


# --- A teacher's at-risk list, in their own subjects only ------------------


def _seed_offering_grades(session, offering, values):
    """Write one grade per learner on an offering. Never committed — the
    session fixture rolls back."""
    from app.models.grades import TermGrade
    from app.models.learners import Enrollment

    roster = (
        session.query(Enrollment)
        .filter_by(
            school_year_id=offering.school_year_id, section_id=offering.section_id
        )
        .limit(len(values))
        .all()
    )
    if len(roster) < len(values):
        pytest.skip("not enough learners in that section")
    for enrollment, value in zip(roster, values):
        session.add(
            TermGrade(
                enrollment_id=enrollment.id,
                section_subject_offering_id=offering.id,
                term_id=offering.term_id,
                official_grade=Decimal(str(value)),
            )
        )
    session.flush()
    return roster


def test_a_teacher_with_no_classes_sees_nobody(session, school_year):
    report = subject_learners_at_risk(session, school_year.id, ())
    assert report.rows == ()
    assert report.learners == 0
    assert report.passing_grade > 0, "the threshold should still resolve"


def test_only_grades_below_the_mark_appear_and_the_mark_itself_passes(
    session, school_year
):
    """75 passes. The boundary is the easy thing to get wrong, and it
    would mislabel a learner who is exactly at the line."""
    from app.models.subjects import SectionSubjectOffering

    offering = (
        session.query(SectionSubjectOffering)
        .filter_by(school_year_id=school_year.id)
        .first()
    )
    if offering is None:
        pytest.skip("no offerings")

    before = len(subject_learners_at_risk(session, school_year.id, (offering.id,)).rows)
    roster = _seed_offering_grades(session, offering, [68, 74, 75, 80])

    report = subject_learners_at_risk(session, school_year.id, (offering.id,))
    flagged = {r.enrollment_id: r for r in report.rows}

    assert len(report.rows) - before == 2, "only 68 and 74 should flag"
    assert roster[0].id in flagged and roster[1].id in flagged
    assert roster[2].id not in flagged, "the passing mark itself must pass"
    assert roster[3].id not in flagged
    assert flagged[roster[0].id].shortfall == 7.0


def test_a_teacher_never_sees_grades_from_a_class_they_do_not_hold(
    session, school_year
):
    """**The property this whole function exists for.**

    Two offerings in the same section, grades written to both, but the
    teacher holds only one. The other subject's failing learners must not
    appear — a subject teacher seeing how their learners do in a
    colleague's class is the leak, and scoping by section would produce
    exactly that.
    """
    from app.models.subjects import SectionSubjectOffering

    section_id = (
        session.query(SectionSubjectOffering.section_id)
        .filter_by(school_year_id=school_year.id)
        .first()
    )
    if section_id is None:
        pytest.skip("no offerings")
    offerings = (
        session.query(SectionSubjectOffering)
        .filter_by(school_year_id=school_year.id, section_id=section_id[0])
        .limit(2)
        .all()
    )
    if len(offerings) < 2:
        pytest.skip("that section runs only one offering")

    mine, theirs = offerings[0], offerings[1]
    _seed_offering_grades(session, mine, [60, 61])
    _seed_offering_grades(session, theirs, [62, 63])

    report = subject_learners_at_risk(session, school_year.id, (mine.id,))
    seen = {r.offering_id for r in report.rows}

    assert mine.id in seen
    assert theirs.id not in seen, (
        "a grade from an offering the teacher does not hold reached their list"
    )


def test_a_teachers_list_never_reads_the_term_summaries(session, school_year):
    """Structural, not behavioural: `at_risk_learners` is built from
    `term_grade_summaries`, which describe a learner across every
    subject. The teacher-facing function must not touch that table, and a
    future edit that "reuses" it would be the leak."""
    import inspect

    source = inspect.getsource(analytics_service.subject_learners_at_risk)
    assert "TermGradeSummary" not in source
    assert "at_risk_learners" not in source


def test_learners_and_rows_are_counted_separately(session, school_year):
    """A learner below the mark in two of a teacher's subjects is two
    rows and one learner."""
    from app.models.subjects import SectionSubjectOffering

    section_id = (
        session.query(SectionSubjectOffering.section_id)
        .filter_by(school_year_id=school_year.id)
        .first()
    )
    if section_id is None:
        pytest.skip("no offerings")
    offerings = (
        session.query(SectionSubjectOffering)
        .filter_by(school_year_id=school_year.id, section_id=section_id[0])
        .limit(2)
        .all()
    )
    if len(offerings) < 2:
        pytest.skip("that section runs only one offering")

    # The same two learners fail both subjects.
    _seed_offering_grades(session, offerings[0], [60, 61])
    _seed_offering_grades(session, offerings[1], [62, 63])

    ids = tuple(o.id for o in offerings)
    report = subject_learners_at_risk(session, school_year.id, ids)
    assert len(report.rows) == 4
    assert report.learners == 2, "the same learner twice is one person"


def test_an_ungraded_learner_is_not_at_risk(session, school_year):
    """Rule 2 once more: no grade is not a low grade."""
    from app.models.subjects import SectionSubjectOffering

    offering = (
        session.query(SectionSubjectOffering)
        .filter_by(school_year_id=school_year.id)
        .first()
    )
    if offering is None:
        pytest.skip("no offerings")
    before = subject_learners_at_risk(session, school_year.id, (offering.id,))
    # Nothing seeded: the roster exists and is entirely ungraded.
    assert before.rows == () or all(r.grade is not None for r in before.rows)


def test_the_subject_risk_cost_is_flat(session, school_year):
    from tests.test_query_cost import QueryCounter

    teacher = _any_teacher(session, school_year)
    ids = taught_offering_ids(session, school_year.id, teacher)
    subject_learners_at_risk(session, school_year.id, ids)  # warm
    with QueryCounter() as counter:
        subject_learners_at_risk(session, school_year.id, ids)
    assert counter.count <= 10, f"{counter.count} queries for a teacher's at-risk list"


# --- Annual standing -------------------------------------------------------


def test_annual_risk_respects_the_scope(session, school_year):
    assert annual_risk(session, school_year.id, ()).sections == ()
    assert annual_risk(session, school_year.id, ()).flagged == ()

    section = _any_advised_section(session, school_year)
    ids = advised_section_ids(session, school_year.id, str(section.adviser_user_id))
    scoped = annual_risk(session, school_year.id, ids)
    assert {s.section_id for s in scoped.sections} <= set(ids)
    assert {r.section_id for r in scoped.flagged} <= set(ids)


def test_annual_risk_reports_completion_per_section(session, school_year):
    """Incomplete records are the other thing that blocks a year closing,
    and right now that is nearly every learner — so they are counted per
    section rather than listed by name."""
    report = annual_risk(session, school_year.id)
    if not report.sections:
        pytest.skip("no sections")
    for row in report.sections:
        assert row.complete + row.incomplete == row.learners
        assert 0.0 <= (row.complete_rate or 0.0) <= 100.0


def test_the_annual_cost_is_flat(session, school_year):
    from tests.test_query_cost import QueryCounter

    annual_risk(session, school_year.id)  # warm
    with QueryCounter() as counter:
        annual_risk(session, school_year.id)
    assert counter.count <= 14, f"{counter.count} queries for annual standing"


def test_a_failing_general_average_flags_and_a_passing_one_does_not(
    session, school_year
):
    """Against the real query, written and rolled back."""
    from app.models.enums import AveragingMethod, CompletionStatus
    from app.models.grades import AnnualGradeSummary
    from app.models.learners import Enrollment

    section = _any_advised_section(session, school_year)
    roster = (
        session.query(Enrollment)
        .filter_by(school_year_id=school_year.id, section_id=section.id)
        .limit(3)
        .all()
    )
    if len(roster) < 3:
        pytest.skip("needs three learners")
    taken = {
        s.enrollment_id
        for s in session.query(AnnualGradeSummary)
        .filter(AnnualGradeSummary.enrollment_id.in_([e.id for e in roster]))
        .all()
    }
    roster = [e for e in roster if e.id not in taken]
    if len(roster) < 3:
        pytest.skip("those learners already have annual summaries")

    cases = [
        (Decimal("72"), Decimal("60"), 2),   # failing average and subjects
        (Decimal("88"), Decimal("80"), 1),   # passing average, one failed subject
        (Decimal("90"), Decimal("85"), 0),   # healthy
    ]
    for enrollment, (average, lowest, failed) in zip(roster, cases):
        session.add(
            AnnualGradeSummary(
                enrollment_id=enrollment.id,
                school_year_id=school_year.id,
                general_average=average,
                lowest_final_grade=lowest,
                failed_subject_count=failed,
                completion_status=CompletionStatus.COMPLETE,
                averaging_method=AveragingMethod.UNIT_WEIGHTED,
                total_units=Decimal("40"),
            )
        )
    session.flush()  # never committed

    report = annual_risk(session, school_year.id, (section.id,))
    flagged = {r.enrollment_id: r for r in report.flagged}

    assert roster[0].id in flagged, "a failing General Average must flag"
    assert roster[1].id in flagged, "a failed subject must flag even at a good average"
    assert roster[2].id not in flagged, "a passing learner must not flag"
    assert flagged[roster[0].id].general_average == 72.0
    assert flagged[roster[0].id].averaging_method == "UNIT_WEIGHTED"
    assert flagged[roster[0].id].total_units == 40.0
    # Worst first: two failed subjects before one.
    order = [r.enrollment_id for r in report.flagged]
    assert order.index(roster[0].id) < order.index(roster[1].id)


def test_a_null_general_average_is_not_a_failing_one(session, school_year):
    """Rule 2 again: a learner nobody has graded has no average, and an
    absent average must never compare as low."""
    from app.models.enums import CompletionStatus
    from app.models.grades import AnnualGradeSummary
    from app.models.learners import Enrollment

    section = _any_advised_section(session, school_year)
    existing = {
        s.enrollment_id
        for s in session.query(AnnualGradeSummary).all()
    }
    enrollment = next(
        (
            e
            for e in session.query(Enrollment)
            .filter_by(school_year_id=school_year.id, section_id=section.id)
            .all()
            if e.id not in existing
        ),
        None,
    )
    if enrollment is None:
        pytest.skip("no learner without an annual summary")

    session.add(
        AnnualGradeSummary(
            enrollment_id=enrollment.id,
            school_year_id=school_year.id,
            general_average=None,
            lowest_final_grade=None,
            failed_subject_count=0,
            completion_status=CompletionStatus.INCOMPLETE,
        )
    )
    session.flush()

    report = annual_risk(session, school_year.id, (section.id,))
    assert enrollment.id not in {r.enrollment_id for r in report.flagged}


def test_the_failed_area_list_collapses_the_language_pair(session, school_year):
    """**§16, in the place it is easiest to get wrong.**

    `subject_final_grades` carries a row for each of the two Grade 11
    language components, but neither is what counts — the combined
    learning area's result is, once. A list built from the raw failed
    rows would report a learner as failing two languages when the pair
    as one area passed.

    Writes finals for both components marked FAILED and a combined
    result marked PASSED, then asserts neither component name appears.
    Rolled back; never committed.
    """
    from app.models.enums import AveragingMethod, CompletionStatus, SubjectRemark
    from app.models.grades import (
        AnnualGradeSummary,
        CombinedLearningAreaResult,
        SubjectFinalGrade,
    )
    from app.models.learners import Enrollment
    from app.models.subjects import (
        CombinedLearningArea,
        CombinedLearningAreaComponent,
        Subject,
    )

    area = session.query(CombinedLearningArea).first()
    if area is None:
        pytest.skip("no combined learning area configured")
    components = (
        session.query(CombinedLearningAreaComponent)
        .filter_by(combined_learning_area_id=area.id)
        .all()
    )
    if len(components) < 2:
        pytest.skip("the combined area has fewer than two components")

    section = _any_advised_section(session, school_year)
    taken = {s.enrollment_id for s in session.query(AnnualGradeSummary).all()}
    enrollment = next(
        (
            e
            for e in session.query(Enrollment)
            .filter_by(school_year_id=school_year.id, section_id=section.id)
            .all()
            if e.id not in taken
        ),
        None,
    )
    if enrollment is None:
        pytest.skip("no learner without an annual summary")

    session.add(
        AnnualGradeSummary(
            enrollment_id=enrollment.id,
            school_year_id=school_year.id,
            general_average=Decimal("74"),
            lowest_final_grade=Decimal("70"),
            failed_subject_count=1,
            completion_status=CompletionStatus.COMPLETE,
            averaging_method=AveragingMethod.UNIT_WEIGHTED,
        )
    )
    for component in components:
        session.add(
            SubjectFinalGrade(
                enrollment_id=enrollment.id,
                subject_id=component.subject_id,
                school_year_id=school_year.id,
                final_grade=Decimal("74"),
                remark=SubjectRemark.FAILED,
            )
        )
    session.add(
        CombinedLearningAreaResult(
            enrollment_id=enrollment.id,
            combined_learning_area_id=area.id,
            school_year_id=school_year.id,
            final_grade=Decimal("76"),
            remark=SubjectRemark.PASSED,
        )
    )
    session.flush()

    report = annual_risk(session, school_year.id, (section.id,))
    row = next(r for r in report.flagged if r.enrollment_id == enrollment.id)

    component_names = {
        session.get(Subject, c.subject_id).official_name for c in components
    }
    assert not (set(row.failed_areas) & component_names), (
        "a language component must never appear on its own — the pair is "
        f"one learning area, and it passed. Got {row.failed_areas}"
    )
    assert area.name not in row.failed_areas, "the pair passed, so it must not be listed"


def test_a_failed_language_pair_is_listed_once_by_its_own_name(session, school_year):
    """The other direction: the pair failed, so it appears once, under
    the combined area's name rather than as two components."""
    from app.models.enums import AveragingMethod, CompletionStatus, SubjectRemark
    from app.models.grades import (
        AnnualGradeSummary,
        CombinedLearningAreaResult,
        SubjectFinalGrade,
    )
    from app.models.learners import Enrollment
    from app.models.subjects import CombinedLearningArea, CombinedLearningAreaComponent

    area = session.query(CombinedLearningArea).first()
    if area is None:
        pytest.skip("no combined learning area configured")
    components = (
        session.query(CombinedLearningAreaComponent)
        .filter_by(combined_learning_area_id=area.id)
        .all()
    )
    section = _any_advised_section(session, school_year)
    taken = {s.enrollment_id for s in session.query(AnnualGradeSummary).all()}
    enrollment = next(
        (
            e
            for e in session.query(Enrollment)
            .filter_by(school_year_id=school_year.id, section_id=section.id)
            .all()
            if e.id not in taken
        ),
        None,
    )
    if enrollment is None:
        pytest.skip("no learner without an annual summary")

    session.add(
        AnnualGradeSummary(
            enrollment_id=enrollment.id,
            school_year_id=school_year.id,
            general_average=Decimal("74"),
            failed_subject_count=1,
            completion_status=CompletionStatus.COMPLETE,
            averaging_method=AveragingMethod.UNIT_WEIGHTED,
        )
    )
    # Components each scraped a pass; the pair as one area did not.
    for component in components:
        session.add(
            SubjectFinalGrade(
                enrollment_id=enrollment.id,
                subject_id=component.subject_id,
                school_year_id=school_year.id,
                final_grade=Decimal("76"),
                remark=SubjectRemark.PASSED,
            )
        )
    session.add(
        CombinedLearningAreaResult(
            enrollment_id=enrollment.id,
            combined_learning_area_id=area.id,
            school_year_id=school_year.id,
            final_grade=Decimal("74"),
            remark=SubjectRemark.FAILED,
        )
    )
    session.flush()

    report = annual_risk(session, school_year.id, (section.id,))
    row = next(r for r in report.flagged if r.enrollment_id == enrollment.id)
    assert row.failed_areas.count(area.name) == 1, (
        f"the pair should be listed once by its own name; got {row.failed_areas}"
    )


# --- Attendance risk (§31) -------------------------------------------------


def _month_with_class_days(session, school_year):
    from app.attendance_service import months_with_class_days

    months = months_with_class_days(session, school_year.id)
    if not months:
        pytest.skip("no class days on the calendar")
    return months[0]


def test_an_unmarked_month_has_no_absence_rate_rather_than_zero(session, school_year):
    """The trap this metric walks into first.

    `absent / eligible` on a month nobody has encoded is 0%, which reads
    as perfect attendance rather than as an empty sheet. The rate is
    denominated on days somebody has actually marked, so it is None
    until someone has. Rule 2, wearing a different hat.
    """
    year, month = _month_with_class_days(session, school_year)
    report = attendance_risk(session, school_year.id, year, month)
    if report.any_records:
        pytest.skip("this month has attendance encoded")
    assert report.sections, "sections should still be reported"
    for row in report.sections:
        assert row.eligible_days > 0
        assert row.absence_rate is None, "an unmarked month must not read 0% absent"
        assert row.encoded_rate == 0.0
    assert attendance_headline(report)[2] is None


def test_attendance_risk_respects_the_section_scope(session, school_year):
    year, month = _month_with_class_days(session, school_year)
    assert attendance_risk(session, school_year.id, year, month, ()).sections == ()

    section = _any_advised_section(session, school_year)
    ids = advised_section_ids(session, school_year.id, str(section.adviser_user_id))
    scoped = attendance_risk(session, school_year.id, year, month, ids)
    assert {s.section_id for s in scoped.sections} <= set(ids)
    assert {r.section_id for r in scoped.flagged} <= set(ids)


def test_a_month_with_no_class_days_reports_nothing(session, school_year):
    report = attendance_risk(session, school_year.id, 1999, 1)
    assert report.class_days == 0
    assert report.sections == ()
    assert report.flagged == ()


def test_the_attendance_cost_is_flat(session, school_year):
    from tests.test_query_cost import QueryCounter

    year, month = _month_with_class_days(session, school_year)
    attendance_risk(session, school_year.id, year, month)  # warm
    with QueryCounter() as counter:
        attendance_risk(session, school_year.id, year, month)
    assert counter.count <= 10, f"{counter.count} queries for a month of attendance"


def test_five_consecutive_absences_flag_and_four_do_not(session, school_year):
    """§31's rule, against the real query.

    Writes attendance for two learners and **rolls back** — the fixture
    never commits. The run is counted in *class days*, so the weekend
    between two school days does not break it; the engine owns that rule
    and this asserts the analytics layer calls it correctly rather than
    reimplementing it.
    """
    from app.attendance_service import class_days_in_month
    from app.models.attendance import AttendanceRecord
    from app.models.enums import AttendanceStatus
    from app.models.learners import Enrollment

    year, month = _month_with_class_days(session, school_year)
    days = class_days_in_month(session, school_year.id, year, month)
    if len(days) < 8:
        pytest.skip("needs at least eight class days in the month")

    section = _any_advised_section(session, school_year)
    roster = (
        session.query(Enrollment)
        .filter_by(school_year_id=school_year.id, section_id=section.id)
        .limit(2)
        .all()
    )
    if len(roster) < 2:
        pytest.skip("needs two learners in the section")

    before = attendance_risk(session, school_year.id, year, month, (section.id,))
    already = {r.enrollment_id for r in before.flagged}

    # First learner: five absences in a row. Second: four, then present.
    for index, day in enumerate(days[:5]):
        session.add(
            AttendanceRecord(
                enrollment_id=roster[0].id,
                calendar_date_id=day.id,
                status=AttendanceStatus.ABSENT,
            )
        )
    for index, day in enumerate(days[:5]):
        session.add(
            AttendanceRecord(
                enrollment_id=roster[1].id,
                calendar_date_id=day.id,
                status=(
                    AttendanceStatus.ABSENT if index < 4 else AttendanceStatus.PRESENT
                ),
            )
        )
    session.flush()  # visible to this transaction only; never committed

    report = attendance_risk(session, school_year.id, year, month, (section.id,))
    flagged = {r.enrollment_id: r for r in report.flagged}

    assert roster[0].id in flagged, "five consecutive absences must flag"
    assert roster[1].id not in flagged or roster[1].id in already, (
        "four consecutive absences must not flag"
    )
    assert flagged[roster[0].id].longest_run == 5
    assert flagged[roster[0].id].days_absent == 5

    # And the rate is now real, because something has been marked.
    section_row = next(s for s in report.sections if s.section_id == section.id)
    assert section_row.absence_rate is not None
    assert section_row.known_days == section_row.days_present + section_row.days_absent


def test_late_and_cutting_still_count_as_present(session, school_year):
    """The engine's rule (§31): the learner was in school. A version that
    treated LATE as an absence would break a run that should hold, and
    inflate every absence rate."""
    from app.attendance_service import class_days_in_month
    from app.models.attendance import AttendanceRecord
    from app.models.enums import AttendanceStatus
    from app.models.learners import Enrollment

    year, month = _month_with_class_days(session, school_year)
    days = class_days_in_month(session, school_year.id, year, month)
    if len(days) < 6:
        pytest.skip("needs six class days")

    section = _any_advised_section(session, school_year)
    enrollment = (
        session.query(Enrollment)
        .filter_by(school_year_id=school_year.id, section_id=section.id)
        .first()
    )
    if enrollment is None:
        pytest.skip("no learners in the section")

    # Absent, absent, LATE, absent, absent — five absences around a day
    # the learner turned up late, which must break the run.
    pattern = [
        AttendanceStatus.ABSENT,
        AttendanceStatus.ABSENT,
        AttendanceStatus.LATE,
        AttendanceStatus.ABSENT,
        AttendanceStatus.ABSENT,
    ]
    for day, status in zip(days[:5], pattern):
        session.add(
            AttendanceRecord(
                enrollment_id=enrollment.id, calendar_date_id=day.id, status=status
            )
        )
    session.flush()

    report = attendance_risk(session, school_year.id, year, month, (section.id,))
    row = next((r for r in report.flagged if r.enrollment_id == enrollment.id), None)
    assert row is None, "a late day must break the run, so this must not flag"

    section_row = next(s for s in report.sections if s.section_id == section.id)
    assert section_row.days_present >= 1, "LATE must count as a day present"


def test_the_section_absence_rate_is_not_an_average_of_learner_rates():
    """Same percentage trap as everywhere else, on a different metric."""
    from app.analytics_service import AttendanceSectionRow

    row = AttendanceSectionRow(
        section_id=uuid.uuid4(),
        section_name="TEST",
        grade_level_name="Grade 11",
        strand_id=uuid.uuid4(),
        strand_name="STEM",
        learners=2,
        eligible_days=100,
        days_present=90,
        days_absent=10,
        unencoded_days=0,
        flagged=1,
    )
    assert row.known_days == 100
    assert row.absence_rate == 10.0
    assert row.encoded_rate == 100.0


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


# --- Award eligibility (§24) -----------------------------------------------
#
# The one thing these guard above all others: **a learner nobody has
# judged is not an ineligible learner**. `learner_awards` rows exist only
# after someone presses "Compute eligibility for all" on the Awards page,
# so the denominator that matters is the learners judged, not the roster.
# Counting the other way reports a school with no honours when the truth
# is that nobody has run the check — and it looks entirely plausible.


def _award_option(session, school_year, per_term: bool):
    options = award_policy_options(session, school_year.id)
    if not options:
        pytest.skip("no award policy effective for this school year")
    match = next((o for o in options if o.per_term == per_term), None)
    if match is None:
        pytest.skip(f"no {'TERM' if per_term else 'ANNUAL'}-scoped award policy")
    return match


def _award_row(**overrides):
    """An AwardSectionRow with only the counts that matter set."""
    fields = dict(
        section_id=uuid.uuid4(),
        section_name="TEST",
        grade_level_id=uuid.uuid4(),
        grade_level_name="Grade 11",
        strand_id=uuid.uuid4(),
        strand_name="STEM",
        term_id=None,
        term_name="",
        term_number=0,
        learners=40,
        computed=0,
        eligible=0,
        not_eligible=0,
        overridden=0,
        stale=0,
        incomplete_records=0,
    )
    fields.update(overrides)
    return AwardSectionRow(**fields)


def _award_learner(**overrides):
    fields = dict(
        enrollment_id=uuid.uuid4(),
        learner_name="DELA CRUZ, Juan",
        section_id=uuid.uuid4(),
        section_name="TEST",
        grade_level_name="Grade 11",
        term_id=None,
        term_name="",
        award_name="With Honors",
        average=91.0,
        is_override=False,
        stale=False,
    )
    fields.update(overrides)
    return AwardLearnerRow(**fields)


# --- The arithmetic, as pure functions --------------------------------------


def test_the_eligible_share_is_of_those_judged_not_of_the_roster():
    row = _award_row(learners=40, computed=4, eligible=3, not_eligible=1)
    assert row.eligible_rate == 75.0, "3 of the 4 judged, not 3 of 40"
    assert row.computed_rate == 10.0
    assert row.not_computed == 36


def test_an_unjudged_section_has_no_eligible_share_rather_than_zero():
    """Rule 2 wearing another hat: nobody judged is not nobody eligible.
    0.0 would also sort a section nobody has run above one where the
    check ran and found nothing."""
    row = _award_row(learners=40, computed=0)
    assert row.eligible_rate is None
    assert row.computed_rate == 0.0, "the roster share is genuinely zero"


def test_a_section_with_nobody_on_the_roll_has_no_percentage_either():
    assert _award_row(learners=0).computed_rate is None


def test_the_headline_recomputes_the_share_from_the_totals():
    """Never the mean of the section shares — a 5-learner section and a
    45-learner one do not weigh the same. Same mistake as SF2 and SF4."""
    rows = [
        _award_row(learners=5, computed=5, eligible=5),      # 100%
        _award_row(learners=45, computed=45, eligible=5),    # ~11%
    ]
    learners, computed, eligible, share = award_headline(rows)
    assert (learners, computed, eligible) == (50, 50, 10)
    assert share == pytest.approx(20.0)
    assert share != pytest.approx((100.0 + 100.0 * 5 / 45) / 2)


def test_the_headline_share_is_none_while_nothing_has_been_judged():
    _learners, computed, eligible, share = award_headline(
        [_award_row(learners=40, computed=0)]
    )
    assert (computed, eligible) == (0, 0)
    assert share is None


def test_tiers_are_counted_by_the_name_the_award_was_given():
    """Read off `award_name` as stored, never re-derived from the average
    against `tier_thresholds` — the ladder was applied once, at compute
    time, against the version in force then."""
    rows = [
        _award_learner(award_name="With Honors", average=91.0),
        _award_learner(award_name="With Honors", average=92.0),
        _award_learner(award_name="With High Honors", average=96.0),
    ]
    assert award_tiers(rows) == [("With Honors", 2), ("With High Honors", 1)]


def test_tiers_are_recomputed_from_the_rows_in_view():
    assert award_tiers([]) == []


# --- Against the live database ---------------------------------------------


def test_an_unknown_policy_version_is_none_not_an_empty_report(session, school_year):
    """A missing question, not an answer of zero — an empty report would
    render as "nobody is eligible"."""
    assert award_eligibility(session, school_year.id, uuid.uuid4()) is None


def test_award_eligibility_respects_the_scope(session, school_year):
    option = _award_option(session, school_year, per_term=False)

    empty = award_eligibility(session, school_year.id, option.version_id, ())
    assert empty is not None
    assert empty.sections == ()
    assert empty.eligible == ()

    section = _any_advised_section(session, school_year)
    ids = advised_section_ids(session, school_year.id, str(section.adviser_user_id))
    scoped = award_eligibility(session, school_year.id, option.version_id, ids)
    assert {r.section_id for r in scoped.sections} <= set(ids)
    assert {r.section_id for r in scoped.eligible} <= set(ids)

    everything = award_eligibility(session, school_year.id, option.version_id, None)
    assert len(everything.sections) > len(scoped.sections), "scoping narrowed nothing"


def test_a_roster_nobody_has_judged_reports_no_share(session, school_year):
    """The state the whole school is in today, and the one most likely to
    be reported wrongly."""
    option = _award_option(session, school_year, per_term=False)
    report = award_eligibility(session, school_year.id, option.version_id)
    if not report.sections or report.any_computed:
        pytest.skip("some eligibility has been computed")
    for row in report.sections:
        assert row.computed == 0
        assert row.eligible_rate is None, "unjudged must not read as 0% eligible"
        assert row.not_computed == row.learners


def test_an_annual_policy_has_one_row_per_section_and_no_term(
    session, school_year
):
    option = _award_option(session, school_year, per_term=False)
    report = award_eligibility(session, school_year.id, option.version_id)
    if not report.sections:
        pytest.skip("no sections")
    keys = [(r.section_id, r.term_id) for r in report.sections]
    assert len(keys) == len(set(keys)), "a section appears twice"
    assert all(r.term_id is None for r in report.sections)


def test_a_term_policy_has_a_row_per_section_and_term(session, school_year):
    from app.models.organization import Term

    option = _award_option(session, school_year, per_term=True)
    terms = session.query(Term).filter_by(school_year_id=school_year.id).count()
    if not terms:
        pytest.skip("no terms")
    report = award_eligibility(session, school_year.id, option.version_id)
    if not report.sections:
        pytest.skip("no sections")
    per_section: dict = {}
    for row in report.sections:
        per_section.setdefault(row.section_id, set()).add(row.term_id)
    assert all(len(v) == terms for v in per_section.values())
    assert all(r.term_id is not None for r in report.sections)


def test_the_award_cost_is_flat(session, school_year):
    from tests.test_query_cost import QueryCounter

    option = _award_option(session, school_year, per_term=False)
    award_eligibility(session, school_year.id, option.version_id)  # warm
    with QueryCounter() as counter:
        award_eligibility(session, school_year.id, option.version_id)
    assert counter.count <= 12, f"{counter.count} queries for award eligibility"


def _three_unjudged_enrollments(session, school_year, section, version_id):
    """Three learners in `section` with no annual summary and no award row
    for this policy — so the writes below are the only ones in play."""
    from app.models.awards import LearnerAward
    from app.models.grades import AnnualGradeSummary
    from app.models.learners import Enrollment

    roster = (
        session.query(Enrollment)
        .filter_by(school_year_id=school_year.id, section_id=section.id)
        .filter(Enrollment.enrollment_status.in_(ACTIVE_ENROLLMENT_STATUSES))
        .limit(8)
        .all()
    )
    ids = [e.id for e in roster]
    if not ids:
        pytest.skip("no learners in this section")
    taken = {
        row[0]
        for row in session.query(AnnualGradeSummary.enrollment_id)
        .filter(AnnualGradeSummary.enrollment_id.in_(ids))
        .all()
    } | {
        row[0]
        for row in session.query(LearnerAward.enrollment_id)
        .filter(
            LearnerAward.enrollment_id.in_(ids),
            LearnerAward.award_policy_version_id == version_id,
        )
        .all()
    }
    free = [e for e in roster if e.id not in taken]
    if len(free) < 3:
        pytest.skip("needs three learners with no summary and no award yet")
    return free[:3]


def test_eligible_not_eligible_and_overridden_are_counted_apart(
    session, school_year
):
    """§40/§67: an override is a human decision with an audited reason.
    Folding it into the policy's eligible count would make an
    administrator's call indistinguishable from the engine's — the one
    thing the audit trail exists to keep separate.

    Written, flushed, asserted, rolled back. Never committed.
    """
    from datetime import datetime, timedelta, timezone

    from app.models.awards import LearnerAward
    from app.models.enums import AveragingMethod, AwardResult, CompletionStatus
    from app.models.grades import AnnualGradeSummary

    option = _award_option(session, school_year, per_term=False)
    section = _any_advised_section(session, school_year)
    roster = _three_unjudged_enrollments(
        session, school_year, section, option.version_id
    )

    computed_at = datetime.now(timezone.utc) - timedelta(days=1)
    cases = [
        (Decimal("96"), AwardResult.ELIGIBLE_AWARDED, "Academic Excellence", False),
        (Decimal("81"), AwardResult.NOT_ELIGIBLE, None, False),
        (Decimal("74"), AwardResult.ELIGIBLE_AWARDED, "Academic Excellence", True),
    ]
    for enrollment, (average, result, name, override) in zip(roster, cases):
        session.add(
            AnnualGradeSummary(
                enrollment_id=enrollment.id,
                school_year_id=school_year.id,
                general_average=average,
                lowest_final_grade=average,
                failed_subject_count=0,
                completion_status=CompletionStatus.COMPLETE,
                averaging_method=AveragingMethod.UNIT_WEIGHTED,
                total_units=Decimal("40"),
                computed_at=computed_at - timedelta(days=1),
            )
        )
        session.add(
            LearnerAward(
                enrollment_id=enrollment.id,
                school_year_id=school_year.id,
                award_policy_version_id=option.version_id,
                term_id=None,
                award_result=result,
                award_name=name,
                reason="test",
                is_override=override,
                computed_at=computed_at,
            )
        )
    session.flush()

    report = award_eligibility(
        session, school_year.id, option.version_id, (section.id,)
    )
    row = next(r for r in report.sections if r.section_id == section.id)
    assert row.computed == 3
    assert row.eligible == 2
    assert row.not_eligible == 1
    assert row.overridden == 1, "the override is counted on its own axis"
    assert row.eligible_rate == pytest.approx(100.0 * 2 / 3)

    named = {r.enrollment_id: r for r in report.eligible}
    assert set(named) == {roster[0].id, roster[2].id}
    assert named[roster[0].id].average == 96.0
    assert named[roster[0].id].is_override is False
    assert named[roster[2].id].is_override is True
    # Highest average first — it is an honour roll.
    assert [r.enrollment_id for r in report.eligible][0] == roster[0].id


def test_an_award_judged_before_the_average_moved_is_flagged_stale(
    session, school_year
):
    """A stale award is worse than a missing one because it looks
    answered: the stored result describes a grade set that has since
    changed. Flagged, never silently recomputed — this page writes
    nothing.
    """
    from datetime import datetime, timedelta, timezone

    from app.models.awards import LearnerAward
    from app.models.enums import AveragingMethod, AwardResult, CompletionStatus
    from app.models.grades import AnnualGradeSummary

    option = _award_option(session, school_year, per_term=False)
    section = _any_advised_section(session, school_year)
    roster = _three_unjudged_enrollments(
        session, school_year, section, option.version_id
    )

    now = datetime.now(timezone.utc)
    # (award judged at, summary last computed at, overridden)
    cases = [
        (now - timedelta(hours=2), now, False),   # stale: the average moved after
        (now, now - timedelta(hours=2), False),   # fresh: judged after the average
        (now - timedelta(hours=2), now, True),    # overridden: never stale
    ]
    for enrollment, (judged, summarised, override) in zip(roster, cases):
        session.add(
            AnnualGradeSummary(
                enrollment_id=enrollment.id,
                school_year_id=school_year.id,
                general_average=Decimal("95"),
                lowest_final_grade=Decimal("90"),
                failed_subject_count=0,
                completion_status=CompletionStatus.COMPLETE,
                averaging_method=AveragingMethod.UNIT_WEIGHTED,
                total_units=Decimal("40"),
                computed_at=summarised,
            )
        )
        session.add(
            LearnerAward(
                enrollment_id=enrollment.id,
                school_year_id=school_year.id,
                award_policy_version_id=option.version_id,
                term_id=None,
                award_result=AwardResult.ELIGIBLE_AWARDED,
                award_name="Academic Excellence",
                reason="test",
                is_override=override,
                computed_at=judged,
            )
        )
    session.flush()

    report = award_eligibility(
        session, school_year.id, option.version_id, (section.id,)
    )
    row = next(r for r in report.sections if r.section_id == section.id)
    assert row.stale == 1, "only the one judged before its average moved"

    by_enrollment = {r.enrollment_id: r for r in report.eligible}
    assert by_enrollment[roster[0].id].stale is True
    assert by_enrollment[roster[1].id].stale is False
    assert by_enrollment[roster[2].id].stale is False, (
        "an overridden award is not waiting for a recompute"
    )


def test_a_term_scoped_row_is_not_counted_under_an_annual_policy(
    session, school_year
):
    """`learner_awards.term_id` is what separates the two scopes. A row
    of the wrong shape answers a different question, so it is left out
    rather than counted into this one."""
    from datetime import datetime, timezone

    from app.models.awards import LearnerAward
    from app.models.enums import AwardResult
    from app.models.organization import Term

    option = _award_option(session, school_year, per_term=False)
    section = _any_advised_section(session, school_year)
    roster = _three_unjudged_enrollments(
        session, school_year, section, option.version_id
    )
    term = (
        session.query(Term)
        .filter_by(school_year_id=school_year.id)
        .order_by(Term.term_number)
        .first()
    )
    if term is None:
        pytest.skip("no terms")

    session.add(
        LearnerAward(
            enrollment_id=roster[0].id,
            school_year_id=school_year.id,
            award_policy_version_id=option.version_id,
            term_id=term.id,
            award_result=AwardResult.ELIGIBLE_AWARDED,
            award_name="Academic Excellence",
            reason="test",
            computed_at=datetime.now(timezone.utc),
        )
    )
    session.flush()

    report = award_eligibility(
        session, school_year.id, option.version_id, (section.id,)
    )
    row = next(r for r in report.sections if r.section_id == section.id)
    assert row.computed == 0
    assert roster[0].id not in {r.enrollment_id for r in report.eligible}


def test_the_named_list_never_leaves_the_scope(session, school_year):
    """This metric names people, so the scope has to hold in SQL rather
    than in a filter afterwards — the same rule as `_at_risk`."""
    option = _award_option(session, school_year, per_term=False)
    section = _any_advised_section(session, school_year)
    ids = advised_section_ids(session, school_year.id, str(section.adviser_user_id))
    report = award_eligibility(session, school_year.id, option.version_id, ids)
    assert {r.section_id for r in report.eligible} <= set(ids)


def test_award_policy_options_describe_both_scopes(session, school_year):
    options = award_policy_options(session, school_year.id)
    if not options:
        pytest.skip("no award policy effective for this school year")
    for option in options:
        assert option.label.endswith(f"(v{option.version_number})")
        assert option.per_term == (option.scope == "TERM")
        assert option.average_label == (
            "Term Average" if option.per_term else "General Average"
        )
