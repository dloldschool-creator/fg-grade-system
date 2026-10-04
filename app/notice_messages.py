"""The words of every parent notice (spec §78.4–78.5).

The approved wording is `docs/parent-notice-templates.md`; this module is
its executable copy, so a change of wording happens in both places.
Pure: strings in, strings out — no database, no mail server.

Names are stored UPPERCASE (`app.naming`). In running prose they read
better in normal case, so prose uses `prose_name`; the signature and the
subject line keep the stored form, as every school form does.
"""

from dataclasses import dataclass

SCHOOL_LINE = "Francisco G. Nepomuceno Memorial High School – Senior High School"


@dataclass(frozen=True)
class NoticeContext:
    learner_first: str
    learner_last: str
    section: str  # "BEZOS"
    grade_level: str  # "Grade 11"
    term_name: str  # "Term 1"
    school_year: str  # "2026-2027"
    adviser_name: str  # as stored, "MARIA SANTOS"

    @property
    def learner_prose(self) -> str:
        return prose_name(f"{self.learner_first} {self.learner_last}")

    @property
    def learner_upper(self) -> str:
        return f"{self.learner_first} {self.learner_last}".upper()

    @property
    def grade_section(self) -> str:
        return f"{self.grade_level} – {self.section}"

    @property
    def section_label(self) -> str:
        """"11-BEZOS": the grade number, if the level has one, and the section."""
        digits = "".join(ch for ch in self.grade_level if ch.isdigit())
        return f"{digits}-{self.section}" if digits else self.section

    @property
    def school_year_dashed(self) -> str:
        return self.school_year.replace("-", "–")


def prose_name(name: str) -> str:
    """"JUAN DELA CRUZ" -> "Juan Dela Cruz". Plain title case; a name with
    unusual capitals (McArthur) reads slightly off, never wrongly."""
    return " ".join(part.capitalize() for part in name.split())


def sender_display_name(adviser_name: str) -> str:
    return f"{prose_name(adviser_name)} (FGNMHS Adviser)"


def _signature(ctx: NoticeContext) -> str:
    return (
        f"{prose_name(ctx.adviser_name)}\n"
        f"Class Adviser / Tagapayo ng Klase, {ctx.section_label}\n"
        f"{SCHOOL_LINE}"
    )


def term_card_email(ctx: NoticeContext) -> tuple[str, str]:
    """Subject and plain-text body of the term-card email (§78.4).

    The password's *format* is stated, never its value.
    """
    subject = f"{ctx.term_name} Temporary Report Card – {ctx.learner_upper} ({ctx.section_label})"
    body = (
        "Good day!\n\n"
        f"Attached is the {ctx.term_name} temporary report card of {ctx.learner_prose} "
        f"of {ctx.grade_section}, School Year {ctx.school_year_dashed}. The file is "
        "password-protected for your child's privacy. The password is your child's "
        "birthdate written as YYYYMMDD. For example, March 5, 2009 is entered as "
        "20090305. For questions, simply reply to this email.\n\n"
        "---\n\n"
        "Magandang araw po!\n\n"
        f"Kalakip po nito ang pansamantalang report card ni {ctx.learner_prose} ng "
        f"{ctx.grade_section} para sa {ctx.term_name}, Taong Panuruan "
        f"{ctx.school_year_dashed}. Para sa privacy ng inyong anak, may password po ang "
        "file: ang kaarawan ng inyong anak sa anyong YYYYMMDD. Halimbawa, kung "
        "Marso 5, 2009, ilagay ang 20090305. Kung may katanungan po, i-reply lamang "
        "ang email na ito.\n\n"
        f"{_signature(ctx)}\n"
    )
    return subject, body


def attachment_filename(ctx: NoticeContext) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in ctx.learner_upper).strip("_")
    term = ctx.term_name.replace(" ", "")
    return f"{term}_TermCard_{safe}.pdf"


def birthdate_password(birthdate) -> str:
    """The PDF password: the learner's birthdate as YYYYMMDD (§78.4)."""
    return birthdate.strftime("%Y%m%d")
