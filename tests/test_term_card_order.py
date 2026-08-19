"""What order a term card lists a learner's subjects in.

The card printed **alphabetically** until 2026-08-17, which is nobody's
order: it put Events Management Services NC III above General Mathematics
and split the language pair to opposite ends of the list, while the
Section Subject Offerings page, the gradebook and the teachers' own
workbook all showed the profile's numbered order. `display_order` was
being carried correctly all the way through to `ReportCardContext` — the
one line that consumed it sorted on the name instead.

Pure: a `ReportCardContext` is built by hand, so these run without a
database and pin the decision rather than the plumbing.
"""

from decimal import Decimal
from types import SimpleNamespace

from app.report_card import ReportCardContext, build_term_subject_rows

D = Decimal


def _context(subjects: list[tuple[str, str, int, list[int]]]) -> ReportCardContext:
    """(subject_id, name, display_order, [term numbers it runs])."""
    offerings_by_subject = {}
    subject_order = {}
    subject_rows = {}
    for subject_id, name, order, terms in subjects:
        offerings_by_subject[subject_id] = {t: f"{subject_id}-t{t}" for t in terms}
        subject_order[subject_id] = order
        subject_rows[subject_id] = SimpleNamespace(id=subject_id, official_name=name)
    return ReportCardContext(
        offerings_by_subject=offerings_by_subject,
        subject_order=subject_order,
        subjects=subject_rows,
        areas=[],
        term_grades={},
        finals={},
        combined={},
    )


ENROLLMENT = SimpleNamespace(id="enrollment-1")

# COWIE's real Grade 11 offerings, in the order the subject profile
# numbers them — deliberately not alphabetical in either direction.
COWIE = [
    ("eff", "Effective Communication", 1, [1, 2, 3]),
    ("mab", "Mabisang Komunikasyon", 2, [1, 2, 3]),
    ("math", "General Mathematics", 3, [1, 2, 3]),
    ("sci", "General Science", 4, [1, 2, 3]),
    ("lcs", "Life and Career Skills", 5, [1, 2, 3]),
    ("kas", "Pag-aaral ng Kasaysayan at Lipunang Pilipino", 6, [1, 2, 3]),
    ("ems", "Events Management Services NC III", 7, [1, 2, 3]),
]


def test_subjects_print_in_the_sections_own_order():
    rows = build_term_subject_rows(None, ENROLLMENT, 1, _context(COWIE))

    assert [name for name, _ in rows] == [name for _, name, _, _ in COWIE]


def test_the_order_is_not_alphabetical():
    """The bug, stated as its own assertion: the two orders differ for
    this section, so a card matching the alphabet is a card ignoring the
    profile."""
    rows = build_term_subject_rows(None, ENROLLMENT, 1, _context(COWIE))
    names = [name for name, _ in rows]

    assert names != sorted(names)


def test_only_the_terms_subjects_are_listed_and_still_in_order():
    """§39 — a one-term elective drops off the other two cards without
    disturbing where everything else sits."""
    subjects = COWIE[:3] + [("elec", "Applied Economics", 4, [2])]
    context = _context(subjects)

    assert [n for n, _ in build_term_subject_rows(None, ENROLLMENT, 2, context)] == [
        "Effective Communication",
        "Mabisang Komunikasyon",
        "General Mathematics",
        "Applied Economics",
    ]
    assert [n for n, _ in build_term_subject_rows(None, ENROLLMENT, 1, context)] == [
        "Effective Communication",
        "Mabisang Komunikasyon",
        "General Mathematics",
    ]


def test_a_subject_with_no_order_set_sorts_last_not_first():
    """An offering added by hand carries display_order 0 from the column
    default only if someone typed it; unset means it is missing from
    `subject_order` entirely. Sending it to the end keeps it from
    displacing the profile's numbering."""
    context = _context(COWIE)
    context.offerings_by_subject["extra"] = {1: "extra-t1"}
    context.subjects["extra"] = SimpleNamespace(id="extra", official_name="Aardvark Studies")

    rows = build_term_subject_rows(None, ENROLLMENT, 1, context)

    assert rows[-1][0] == "Aardvark Studies"


def test_ties_fall_back_to_the_name():
    """Two subjects on the same number is a data problem, not a crash —
    and the card must still print the same way twice."""
    context = _context(
        [
            ("b", "Beta", 5, [1]),
            ("a", "Alpha", 5, [1]),
        ]
    )

    assert [n for n, _ in build_term_subject_rows(None, ENROLLMENT, 1, context)] == [
        "Alpha",
        "Beta",
    ]


def test_grades_travel_with_their_subject():
    """Reordering by a key held in a parallel dict is exactly where a
    grade gets attached to the wrong line."""
    context = _context(COWIE)
    context.term_grades[(ENROLLMENT.id, "ems-t1")] = D(88)
    context.term_grades[(ENROLLMENT.id, "eff-t1")] = D(91)

    rows = dict(build_term_subject_rows(None, ENROLLMENT, 1, context))

    assert rows["Events Management Services NC III"] == D(88)
    assert rows["Effective Communication"] == D(91)
    assert rows["General Mathematics"] is None
