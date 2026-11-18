"""Editing a logged movement in place, and one-per-type (app/movement_edit.py).

The rules are tested pure; the database tests write, flush, assert and roll
back like the rest of the suite — `edit_movement` never commits.
"""

import inspect
import uuid
from datetime import date

import pytest

from app import audit_service
from app.attendance_engine import Movement
from app.models.enums import EnrollmentStatus
from app.movement_edit import (
    EDITABLE_FIELDS,
    ONCE_PER_ENROLLMENT,
    MovementRefused,
    StaleMovement,
    duplicate_problem,
    edit_movement,
    fields_problem,
    normalise_fields,
    snapshot,
)

T_IN = EnrollmentStatus.TRANSFERRED_IN
T_OUT = EnrollmentStatus.TRANSFERRED_OUT
DROPPED = EnrollmentStatus.DROPPED
NLS = EnrollmentStatus.NLS


# --- One of a kind ------------------------------------------------------------


@pytest.mark.parametrize("kind", [T_IN, T_OUT, DROPPED, NLS])
def test_a_second_movement_of_the_same_kind_is_refused(kind):
    log = [Movement(kind, date(2026, 9, 12))]
    problem = duplicate_problem(log, kind)
    assert problem and "Sep 12, 2026" in problem and "edit that entry" in problem


def test_leaving_and_returning_is_allowed():
    """Transferred Out then Transferred In is a real path, so the limit is
    per type and not on exits as a whole."""
    assert duplicate_problem([Movement(T_OUT, date(2026, 9, 12))], T_IN) is None
    assert duplicate_problem([Movement(T_IN, date(2026, 8, 1)), Movement(T_OUT, date(2026, 9, 12))], DROPPED) is None


def test_other_types_are_not_limited():
    log = [Movement(EnrollmentStatus.SHIFTED_IN, date(2026, 8, 1))]
    assert duplicate_problem(log, EnrollmentStatus.SHIFTED_IN) is None
    assert EnrollmentStatus.SHIFTED_IN not in ONCE_PER_ENROLLMENT


def test_an_empty_log_allows_anything():
    for kind in ONCE_PER_ENROLLMENT:
        assert duplicate_problem([], kind) is None


# --- Fields -------------------------------------------------------------------


def test_each_transfer_needs_its_own_school():
    assert fields_problem(T_IN, "", None) and "Previous school" in fields_problem(T_IN, "  ", "X")
    assert "Receiving school" in fields_problem(T_OUT, "X", "")
    assert fields_problem(T_IN, "Some HS", None) is None
    assert fields_problem(T_OUT, None, "Some HS") is None
    assert fields_problem(DROPPED, None, None) is None


def _fields(kind, **overrides):
    base = dict(
        effective_date=date(2026, 9, 12),
        details=None,
        previous_school="From HS",
        receiving_school="To HS",
        nls_reason=None,
        remarks=None,
    )
    return normalise_fields(kind, **{**base, **overrides})


def test_a_school_that_does_not_apply_is_dropped():
    assert _fields(T_IN)["receiving_school"] is None
    assert _fields(T_IN)["previous_school"] == "From HS"
    assert _fields(T_OUT)["previous_school"] is None
    assert _fields(T_OUT)["receiving_school"] == "To HS"


def test_blanks_become_none_and_the_type_is_not_a_field():
    fields = _fields(DROPPED, details="   ", remarks="")
    assert fields["details"] is None and fields["remarks"] is None
    assert "movement_type" not in EDITABLE_FIELDS


# --- Wiring -------------------------------------------------------------------


def test_the_edit_is_audited_with_a_required_reason():
    assert audit_service.LEARNER_MOVEMENT_EDITED in audit_service.REASON_REQUIRED


def test_the_edit_goes_through_the_shared_finalized_month_rule():
    source = inspect.getsource(edit_movement)
    assert "apply_movement_change(" in source and "lock_movements(" in source


def test_the_page_checks_for_a_duplicate_after_locking_the_log():
    from app.admin_pages import enrollment

    source = inspect.getsource(enrollment)
    lock = source.index("log_before = lock_movements(session, enrollment)")
    assert lock < source.index("duplicate_problem(log_before", lock)
    assert "edit_movement(" in source


def test_the_edit_is_listed_in_the_audit_log_filter():
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "app/admin_pages/audit_log.py").read_text(encoding="utf-8")
    assert "LEARNER_MOVEMENT_EDITED" in source


# --- Against the database (rolled back) ---------------------------------------


@pytest.fixture
def db_session():
    from app.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _enrollment_with_movement(session, kind=T_OUT, **extra):
    from app.models.learners import Enrollment, Learner, LearnerMovement

    enrollment = session.query(Enrollment).first()
    assert enrollment is not None, "needs an enrollment"
    learner = session.get(Learner, enrollment.learner_id)
    movement = LearnerMovement(
        enrollment_id=enrollment.id,
        movement_type=kind,
        effective_date=date(2026, 8, 3),
        receiving_school="Wrong School",
        **extra,
    )
    session.add(movement)
    session.flush()
    return enrollment, learner, movement


def _edit(session, enrollment, learner, movement, **changes):
    expected = snapshot(movement)
    new = {**expected, **changes}
    return edit_movement(
        session,
        enrollment=enrollment,
        movement=movement,
        learner=learner,
        expected=expected,
        new=new,
        reason="Typed the wrong school",
        user_id=None,
        allowed_section_ids=None,
    )


def test_an_edit_changes_the_row_in_place_and_audits_the_old_value(db_session):
    from app.models.admin import AuditLog
    from app.models.learners import LearnerMovement

    enrollment, learner, movement = _enrollment_with_movement(db_session)
    count_before = db_session.query(LearnerMovement).filter_by(enrollment_id=enrollment.id).count()
    version = enrollment.version

    _edit(db_session, enrollment, learner, movement, receiving_school="Right School")
    db_session.flush()

    assert movement.receiving_school == "Right School"
    assert movement.movement_type == T_OUT
    assert db_session.query(LearnerMovement).filter_by(enrollment_id=enrollment.id).count() == count_before
    assert enrollment.version == version + 1
    entry = (
        db_session.query(AuditLog)
        .filter_by(action=audit_service.LEARNER_MOVEMENT_EDITED, object_id=movement.id)
        .one()
    )
    assert entry.previous_value["receiving_school"] == "Wrong School"
    assert entry.new_value["receiving_school"] == "Right School"
    assert entry.reason == "Typed the wrong school"


def test_a_stale_edit_is_refused(db_session):
    enrollment, learner, movement = _enrollment_with_movement(db_session)
    expected = snapshot(movement)
    movement.receiving_school = "Changed by someone else"
    db_session.flush()
    with pytest.raises(StaleMovement, match="Someone else"):
        edit_movement(
            db_session,
            enrollment=enrollment,
            movement=movement,
            learner=learner,
            expected=expected,
            new={**expected, "receiving_school": "Mine"},
            reason="Typed the wrong school",
            user_id=None,
            allowed_section_ids=None,
        )


def test_an_edit_that_changes_nothing_or_has_no_reason_is_refused(db_session):
    enrollment, learner, movement = _enrollment_with_movement(db_session)
    with pytest.raises(MovementRefused, match="Nothing"):
        _edit(db_session, enrollment, learner, movement)
    with pytest.raises(MovementRefused):
        edit_movement(
            db_session,
            enrollment=enrollment,
            movement=movement,
            learner=learner,
            expected=snapshot(movement),
            new={**snapshot(movement), "remarks": "x"},
            reason="x",
            user_id=None,
            allowed_section_ids=None,
        )


def test_an_adviser_cannot_edit_outside_their_sections(db_session):
    enrollment, learner, movement = _enrollment_with_movement(db_session)
    with pytest.raises(MovementRefused, match="not in a section you advise"):
        edit_movement(
            db_session,
            enrollment=enrollment,
            movement=movement,
            learner=learner,
            expected=snapshot(movement),
            new={**snapshot(movement), "remarks": "Some remark"},
            reason="Typed the wrong school",
            user_id=None,
            allowed_section_ids={uuid.uuid4()},
        )


def test_a_transfer_out_cannot_lose_its_school(db_session):
    enrollment, learner, movement = _enrollment_with_movement(db_session)
    with pytest.raises(MovementRefused, match="Receiving school"):
        _edit(db_session, enrollment, learner, movement, receiving_school=None)


def test_a_movement_of_another_learner_is_refused(db_session):
    from app.models.learners import Enrollment

    enrollment, learner, movement = _enrollment_with_movement(db_session)
    other = db_session.query(Enrollment).filter(Enrollment.id != enrollment.id).first()
    if other is None:
        pytest.skip("needs two enrollments")
    with pytest.raises(MovementRefused, match="another learner"):
        edit_movement(
            db_session,
            enrollment=other,
            movement=movement,
            learner=learner,
            expected=snapshot(movement),
            new={**snapshot(movement), "remarks": "Some remark"},
            reason="Typed the wrong school",
            user_id=None,
            allowed_section_ids=None,
        )
