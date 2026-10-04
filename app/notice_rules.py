"""Which parent notice a learner gets for a term (spec §78.2–78.3).

Pure and dependency-free: no database, no Streamlit, no `app.models`.
The service gathers the numbers; this decides. Kept apart so every branch
can be tested without a database and so the page, the senders (step 3)
and any later report all read the same rule.

**Not ready is decided first, and the order matters.** A blank grade is
not a passing grade (rule 2) and an unencoded attendance day is not a day
present, so a learner whose record is half-encoded must never be released
merely because nothing *bad* has been encoded yet.
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


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def classify(figures: LearnerTermFigures, thresholds: NoticeThresholds) -> Classification:
    not_ready = []
    if not figures.summary_found:
        not_ready.append("grades not yet saved for this term")
    elif not figures.grades_complete:
        not_ready.append("some grades for this term are still blank")
    elif figures.failed_count is None:
        not_ready.append("grades not yet summarised for this term")
    if figures.eligible_days == 0:
        not_ready.append("no class days on the calendar for this term")
    elif figures.unencoded_days:
        not_ready.append(f"{_plural(figures.unencoded_days, 'attendance day')} not encoded")
    if not_ready:
        return Classification(NoticeGroup.NOT_READY, tuple(not_ready))

    concern = []
    if figures.failed_count:
        concern.append(f"failing {_plural(figures.failed_count, 'subject')}")
    if figures.absences > thresholds.max_absences:
        concern.append(_plural(figures.absences, "absence"))
    if figures.lates > thresholds.max_lates:
        concern.append(_plural(figures.lates, "late"))
    if figures.cuttings > thresholds.max_cuttings:
        concern.append(_plural(figures.cuttings, "cutting"))
    if concern:
        return Classification(NoticeGroup.CONCERN, tuple(concern))
    return Classification(NoticeGroup.RELEASE, ())


def effective_group(computed: NoticeGroup, override: NoticeGroup | None) -> NoticeGroup:
    """An adviser's override moves a learner between Release and Concern
    (§78.3). It never lifts Not ready — the missing grades or attendance
    have to be completed first — and while the learner is Not ready the
    override waits rather than being discarded, so it applies again once
    the record is complete."""
    if computed is NoticeGroup.NOT_READY or override is None:
        return computed
    return override


def may_override(computed: NoticeGroup) -> bool:
    return computed is not NoticeGroup.NOT_READY
