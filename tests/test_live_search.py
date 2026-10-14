"""`app/live_search.py` — the query splitting both live-search pages share,
and the Learner Masterlist's word-by-word match against the live database
(read-only)."""

import pytest

from app.live_search import like_pattern, search_words


def test_search_words_splits_on_spaces_and_commas_and_lowercases():
    assert search_words("Pineda, Anne") == ["pineda", "anne"]
    assert search_words("  PINEDA   anne ") == ["pineda", "anne"]
    assert search_words("") == []
    assert search_words(" , ") == []


def test_like_pattern_escapes_wildcards():
    assert like_pattern("a_b") == r"%a\_b%"
    assert like_pattern("50%") == r"%50\%%"
    assert like_pattern("a\\b") == r"%a\\b%"


def test_masterlist_finds_a_learner_by_both_names_in_either_order():
    from app.admin_pages.learners import _listed_learners
    from app.database import SessionLocal
    from app.models.learners import Learner

    with SessionLocal() as session:
        learner = session.query(Learner).first()
        if learner is None:
            pytest.skip("no learners")
        for query in (
            f"{learner.last_name} {learner.first_name}",
            f"{learner.first_name.lower()} {learner.last_name.lower()}",
        ):
            found = _listed_learners(session, query, None, set())
            assert learner.id in {l.id for l in found}
        none = _listed_learners(session, f"{learner.last_name} zzqqxx-no-such-name", None, set())
        assert none == []
        session.rollback()
