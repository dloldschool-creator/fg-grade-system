"""Which parent notice a learner gets for a term (spec §78.2–78.3).

Pure and dependency-free: no database, no Streamlit, no `app.models`.
The service gathers the numbers; this decides. Kept apart so every branch
can be tested without a database and so the page, the senders (step 3)
and any later report all read the same rule.

**Not ready is decided first, and the order matters.** A blank grade is
not a passing grade (rule 2) and an unencoded attendance day is not a day
present, so a learner whose record is half-encoded must never be released
merely because nothing *bad* has been encoded yet.

**Except attendance already over the limit** (§78.2, amended 2026-10-04).
Encoding more days can only add to a count, so 3 absences on the days
encoded so far is 3 or more however the rest turn out. Such a learner is
Concern at once, which is what lets the adviser contact the parent during
the term. The missing items are still carried (`incomplete`), and while
any remain the learner can't be overridden — overriding them to Release
would email a card with blanks on it.
"""

import enum
from dataclasses import dataclass


class NoticeGroup(str, enum.Enum):
    RELEASE = "RELEASE"
    CONCERN = "CONCERN"
    NOT_READY = "NOT_READY"


GROUP_LABELS = {
    NoticeGroup.RELEASE: "Release",
    NoticeGroup.CONCERN: "Concern",
    NoticeGroup.NOT_READY: "Not ready",
}


@dataclass(frozen=True)
class NoticeThresholds:
    """The most a learner may have and still be released. Read from the
    school year's versioned `parent_notice_policies` row, never hardcoded.
    """

    max_absences: int
    max_lates: int
    max_cuttings: int
    version: int | None = None

    def describe(self) -> str:
        return (
            f"no failing grade, at most {self.max_absences} absence(s), "
            f"at most {self.max_lates} late(s) and "
            + ("no cutting" if self.max_cuttings == 0 else f"at most {self.max_cuttings} cutting(s)")
        )


@dataclass(frozen=True)
class LearnerTermFigures:
    """Everything the rule reads, for one learner in one term.

    `summary_found` is False when no term summary exists yet;
    `grades_complete` mirrors the summary's completion status, which is
    only COMPLETE when every subject running that term carries a grade.
    `failed_count` is read from the summary as stored — None means it was
    never computed, which is not the same as zero.
    """

    summary_found: bool
    grades_complete: bool
    failed_count: int | None
    eligible_days: int
    unencoded_days: int
    absences: int
    lates: int
    cuttings: int


@dataclass(frozen=True)
class Classification:
    group: NoticeGroup
    reasons: tuple[str, ...]
    # What is still missing. Always set for Not ready; set for Concern only
    # when attendance put the learner there before the record was complete.
    incomplete: tuple[str, ...] = ()
    # Concern on attendance — contactable while encoding is open (§78.5).
    attendance_concern: bool = False

    @property
    def record_complete(self) -> bool:
        return not self.incomplete


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def classify(figures: LearnerTermFigures, thresholds: NoticeThresholds) -> Classification:
    missing_grades = []
    if not figures.summary_found:
        missing_grades.append("grades not yet saved for this term")
    elif not figures.grades_complete:
        missing_grades.append("some grades for this term are still blank")
    elif figures.failed_count is None:
        missing_grades.append("grades not yet summarised for this term")
    not_ready = list(missing_grades)
    if figures.eligible_days == 0:
        not_ready.append("no class days on the calendar for this term")
    elif figures.unencoded_days:
        not_ready.append(f"{_plural(figures.unencoded_days, 'attendance day')} not encoded")

    attendance = []
    if figures.absences > thresholds.max_absences:
        attendance.append(_plural(figures.absences, "absence"))
    if figures.lates > thresholds.max_lates:
        attendance.append(_plural(figures.lates, "late"))
    if figures.cuttings > thresholds.max_cuttings:
        attendance.append(_plural(figures.cuttings, "cutting"))

    # A failing count is only read off a complete grade record.
    failing = (
        [f"failing {_plural(figures.failed_count, 'subject')}"]
        if not missing_grades and figures.failed_count
        else []
    )
    if not_ready and not attendance:
        return Classification(NoticeGroup.NOT_READY, tuple(not_ready), incomplete=tuple(not_ready))
    concern = failing + attendance
    if concern:
        return Classification(
            NoticeGroup.CONCERN, tuple(concern),
            incomplete=tuple(not_ready), attendance_concern=bool(attendance),
        )
    return Classification(NoticeGroup.RELEASE, ())


def effective_group(
    computed: NoticeGroup, override: NoticeGroup | None, *, record_complete: bool = True
) -> NoticeGroup:
    """An adviser's override moves a learner between Release and Concern
    (§78.3). It never applies to an incomplete record — Not ready, or
    Concern on attendance with grades or days still missing — and while
    the record is incomplete the override waits rather than being
    discarded, so it applies again once the record is complete."""
    if computed is NoticeGroup.NOT_READY or override is None or not record_complete:
        return computed
    return override


def may_override(computed: NoticeGroup, record_complete: bool = True) -> bool:
    return computed is not NoticeGroup.NOT_READY and record_complete
