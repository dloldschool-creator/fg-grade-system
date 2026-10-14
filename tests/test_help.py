"""The quick guide is only useful if it stays true.

These check the two ways it can silently rot: naming a role that doesn't
exist, and quietly dropping a role that does.
"""

from app import help_content
from app.admin_pages import help as help_page
from app.seed import ROLES

SEEDED = {code for code, _ in ROLES}


def test_every_documented_role_actually_exists():
    """A typo here would silently show a section to nobody, since the
    lookup is by role code."""
    assert set(help_content.BY_ROLE) <= SEEDED


def test_every_role_with_screens_is_documented():
    """Every seeded role now has screens and a guide section.

    This used to allow one exception, ATTENDANCE_ENCODER, which was
    seeded so it could be granted while nothing was ever built for it.
    It was removed on 2026-08-16 — advisers encode their own section's
    attendance — so the exception is gone and a role added without a
    guide entry fails here. Grant-a-role-that-does-nothing was the state
    this test was tolerating; it should not come back silently.
    """
    assert SEEDED == set(help_content.BY_ROLE)


def test_no_section_is_empty():
    assert help_content.UNIVERSAL
    for code, (heading, items) in help_content.BY_ROLE.items():
        assert heading and items, code


def test_every_entry_has_a_topic_title_and_body():
    for where, (topic, title, body) in _all_entries():
        assert topic.strip() and title.strip() and body.strip(), (where, title)


def test_every_role_has_a_label():
    assert set(help_content.ROLE_LABELS) == set(help_content.BY_ROLE)
    assert all(label.strip() for label in help_content.ROLE_LABELS.values())


def _all_entries():
    for entry in help_content.UNIVERSAL:
        yield "UNIVERSAL", entry
    for code, (_, items) in help_content.BY_ROLE.items():
        for entry in items:
            yield code, entry


def test_titles_are_unique_within_a_role():
    """Two entries with one title read as a mistake, and search results
    show the title alone."""
    seen = set()
    for code, (_, title, _) in _all_entries():
        assert (code, title) not in seen, (code, title)
        seen.add((code, title))


def test_a_topic_is_used_at_least_twice_in_its_list():
    """A topic used once is a typo of another topic or a heading over a
    single entry. Applies to every list, UNIVERSAL included; a list that
    is all one topic passes (the page draws no label for it)."""
    lists = {"UNIVERSAL": help_content.UNIVERSAL}
    lists.update({code: items for code, (_, items) in help_content.BY_ROLE.items()})
    for name, items in lists.items():
        counts = {}
        for topic, _, _ in items:
            counts[topic] = counts.get(topic, 0) + 1
        assert all(n >= 2 for n in counts.values()), (name, counts)


def test_grouping_keeps_first_appearance_order_and_every_entry():
    items = [("B", "t1", "x"), ("A", "t2", "y"), ("B", "t3", "z")]
    assert help_page.grouped_by_topic(items) == [
        ("B", [("t1", "x"), ("t3", "z")]),
        ("A", [("t2", "y")]),
    ]


def test_search_needs_every_word_in_any_order_and_only_narrows():
    broad = help_page.search_entries("attendance", [])
    narrower = help_page.search_entries("attendance month", [])
    reordered = help_page.search_entries("month attendance", [])
    assert broad and narrower
    assert len(narrower) < len(broad)
    assert narrower == reordered
    assert help_page.search_entries("zzqqxx-no-such-word", []) == []
    assert help_page.search_entries("   ", []) == []


def test_search_finds_an_entry_by_a_phrase_from_its_title():
    results = help_page.search_entries("blank zero", [])
    assert any(title == "A blank is not a zero" for _, _, title, _ in results)


def test_search_puts_everyones_entries_then_the_readers_role_first():
    results = help_page.search_entries("grade", ["REGISTRAR"])
    wheres = list(dict.fromkeys(where for where, *_ in results))
    registrar = help_content.ROLE_LABELS["REGISTRAR"]
    adviser = help_content.ROLE_LABELS["ADVISER"]
    # Preconditions asserted, not assumed: a guide edit that makes "grade"
    # vanish from one of them must fail here rather than skip the check.
    assert {"Everyone", registrar, adviser} <= set(wheres)
    assert wheres[0] == "Everyone"
    assert wheres.index(registrar) < wheres.index(adviser)


def test_the_role_label_is_searchable_but_its_whole_heading_is_not():
    """The adviser heading mentions attendance, which must not make an
    adviser entry that never says it a match for "attendance"."""
    matched = {title for _, _, title, _ in help_page.search_entries("attendance", [])}
    for topic, title, body in help_content.BY_ROLE["ADVISER"][1]:
        if "attendance" not in f"{topic} {title} {body}".casefold():
            assert title not in matched, title
    assert help_page.search_entries("advisers", [])


def test_a_word_must_start_a_word_not_sit_inside_one():
    """"ncod" is inside "encoded"/"encoding" and matches nothing; as a
    prefix, "transfer" still finds "transferred"."""
    assert help_page.search_entries("ncod", []) == []
    assert help_page.search_entries("transfer", [])
    # "in" as a prefix must narrow: it is no longer inside every long word.
    assert len(help_page.search_entries("in", [])) < len(
        [1 for _ in _all_entries()]
    )
