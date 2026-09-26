"""Clearing an award override re-judges that learner immediately.

It used to only drop the flag, so the overridden result (and its
"Manually overridden: ..." reason) stayed on the row — and on any
certificate printed from it — until someone next pressed Compute. Found
2026-09-26 with three such rows live.

`compute_award_eligibility_batch` commits, so it cannot run against the
live database inside a rolled-back test; it is stubbed here, and what is
asserted is the contract: called once, for this learner's policy version
and term, with the override already off so the batch re-judges the row
instead of skipping it.
"""
import uuid
from types import SimpleNamespace

from app import award_service


def test_clearing_an_override_rejudges_that_learner(monkeypatch):
    award = SimpleNamespace(
        id=uuid.uuid4(),
        enrollment_id=uuid.uuid4(),
        award_policy_version_id=uuid.uuid4(),
        term_id=uuid.uuid4(),
        award_result="ELIGIBLE_AWARDED",
        award_name="CHARACTER TRAIT AWARDS",
        is_override=True,
        override_by_user_id=uuid.uuid4(),
        override_reason="nominated",
    )
    audits, calls = [], []
    monkeypatch.setattr(award_service.audit_service, "record", lambda *a, **k: audits.append(k))

    def fake_batch(session, enrollment_ids, version_id, term_id=None):
        # The flag must already be off, or the real batch skips the row.
        calls.append((award.is_override, enrollment_ids, version_id, term_id))
        return {}

    monkeypatch.setattr(award_service, "compute_award_eligibility_batch", fake_batch)

    award_service.clear_award_override(object(), award, cleared_by_user_id=uuid.uuid4())

    assert calls == [(False, [award.enrollment_id], award.award_policy_version_id, award.term_id)]
    assert award.override_reason is None and award.override_by_user_id is None
    assert [a["action"] for a in audits] == [award_service.audit_service.AWARD_OVERRIDE_CLEARED]


def test_an_annual_award_is_rejudged_without_a_term(monkeypatch):
    award = SimpleNamespace(
        id=uuid.uuid4(),
        enrollment_id=uuid.uuid4(),
        award_policy_version_id=uuid.uuid4(),
        term_id=None,
        award_result="ELIGIBLE_AWARDED",
        is_override=True,
        override_by_user_id=None,
        override_reason="x",
    )
    calls = []
    monkeypatch.setattr(award_service.audit_service, "record", lambda *a, **k: None)
    monkeypatch.setattr(
        award_service,
        "compute_award_eligibility_batch",
        lambda s, ids, vid, term_id=None: calls.append(term_id) or {},
    )
    award_service.clear_award_override(object(), award)
    assert calls == [None]
