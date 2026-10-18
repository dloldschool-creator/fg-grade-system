"""The one rule for "which enrollment is a learner's current one"."""

from app.learner_access import newest_per_learner


def test_the_newest_school_year_wins_and_ties_keep_the_first():
    rows = [
        ("a", "2025-2026", "old-a"),
        ("a", "2026-2027", "new-a"),
        ("b", "2026-2027", "only-b"),
        ("c", "2026-2027", "first-c"),
        ("c", "2026-2027", "second-c"),
        ("d", None, "unnamed-d"),
        ("d", "2025-2026", "named-d"),
    ]
    got = newest_per_learner(rows)
    assert got["a"] == ("2026-2027", "new-a")
    assert got["b"] == ("2026-2027", "only-b")
    assert got["c"] == ("2026-2027", "first-c")
    assert got["d"] == ("2025-2026", "named-d")  # a missing year name sorts first


def test_row_order_does_not_matter():
    rows = [("a", "2026-2027", "new"), ("a", "2025-2026", "old")]
    assert newest_per_learner(rows)["a"] == ("2026-2027", "new")
    assert newest_per_learner(reversed(rows))["a"] == ("2026-2027", "new")
