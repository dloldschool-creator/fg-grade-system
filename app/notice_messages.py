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


# --- Dates and times, in both languages -------------------------------------

_FIL_DAYS = ["Lunes", "Martes", "Miyerkules", "Huwebes", "Biyernes", "Sabado", "Linggo"]
_FIL_DAYS_SHORT = ["Lun", "Mar", "Miy", "Huw", "Biy", "Sab", "Lin"]
_FIL_MONTHS = [
    "Enero", "Pebrero", "Marso", "Abril", "Mayo", "Hunyo",
    "Hulyo", "Agosto", "Setyembre", "Oktubre", "Nobyembre", "Disyembre",
]
_FIL_MONTHS_SHORT = [
    "Ene", "Peb", "Mar", "Abr", "May", "Hun", "Hul", "Ago", "Set", "Okt", "Nob", "Dis",
]


_EN_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_EN_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _clock(value) -> str:
    """9:00 AM, 2:30 PM. Built by hand: strftime's %p and %-I depend on the
    platform and locale, and the host is not this machine."""
    hour = value.hour % 12 or 12
    return f"{hour}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"


# Day and month names are spelt out here rather than taken from strftime's
# %A/%B, which follow the host's locale: the same reason _clock exists.


def date_plain_en(day) -> str:
    """October 5, 2026."""
    return f"{_EN_MONTHS[day.month - 1]} {day.day}, {day.year}"


def date_long_en(day) -> str:
    return f"{_EN_DAYS[day.weekday()]}, {date_plain_en(day)}"


def date_long_fil(day) -> str:
    return f"{_FIL_DAYS[day.weekday()]}, {_FIL_MONTHS[day.month - 1]} {day.day}, {day.year}"


def time_en(value) -> str:
    return _clock(value)


def time_fil(value) -> str:
    """ika-9:00 ng umaga / ika-12:00 ng tanghali / ika-2:30 ng hapon / gabi."""
    hour = value.hour
    if hour < 12:
        period = "umaga"
    elif hour == 12:
        period = "tanghali"
    elif hour < 18:
        period = "hapon"
    else:
        period = "gabi"
    return f"ika-{hour % 12 or 12}:{value.minute:02d} ng {period}"


def date_short_en(day, at) -> str:
    return (
        f"{_EN_DAYS[day.weekday()][:3]}, {_EN_MONTHS[day.month - 1][:3]} "
        f"{day.day}, {_clock(at)}"
    )


def date_short_fil(day, at) -> str:
    return (
        f"{_FIL_DAYS_SHORT[day.weekday()]}, {_FIL_MONTHS_SHORT[day.month - 1]} "
        f"{day.day}, {_clock(at)}"
    )


# --- Concern email (§78.5) ----------------------------------------------------


def concern_email(ctx: NoticeContext, meeting=None) -> tuple[str, str]:
    """Subject and body. `meeting` is (date, time) or None; without one the
    parent is asked to reply or visit instead. General by design: no
    grades, subjects or counts."""
    subject = (
        f"Request to talk about {ctx.learner_prose}'s {ctx.term_name} progress / "
        f"Paanyaya tungkol sa {ctx.term_name} ni {ctx.learner_prose}"
    )
    first = prose_name(ctx.learner_first)
    if meeting:
        day, at = meeting
        english_ask = (
            f"We kindly ask you to come to the school on {date_long_en(day)} at "
            f"{time_en(at)}. If you cannot come at that time, please reply to this "
            "email so we can agree on another schedule."
        )
        filipino_ask = (
            "Magalang po naming hinihiling na kayo ay pumunta sa paaralan sa "
            f"{date_long_fil(day)}, {time_fil(at)}. Kung hindi po kayo makararating sa "
            "oras na ito, mangyari pong i-reply ang email na ito upang makapagtakda tayo "
            "ng ibang araw."
        )
    else:
        english_ask = (
            "Please reply to this email or visit the school at your earliest "
            f"convenience so we can discuss how best to support {first}."
        )
        filipino_ask = (
            "Mangyari po lamang na i-reply ang email na ito o bumisita sa paaralan sa "
            "lalong madaling panahon upang mapag-usapan natin kung paano higit na "
            f"matutulungan si {first}."
        )
    body = (
        "Good day!\n\n"
        f"I would like to talk with you about the {ctx.term_name} progress of "
        f"{ctx.learner_prose} of {ctx.grade_section}. {english_ask}\n\n"
        "Thank you very much.\n\n"
        "---\n\n"
        "Magandang araw po!\n\n"
        f"Nais ko po sanang makausap kayo tungkol sa progreso ni {ctx.learner_prose} ng "
        f"{ctx.grade_section} sa {ctx.term_name}. {filipino_ask}\n\n"
        "Maraming salamat po.\n\n"
        f"{_signature(ctx)}\n"
    )
    return subject, body


# --- SMS (§78.5) --------------------------------------------------------------

# The GSM 03.38 basic character set. One character outside it switches the
# whole text to UCS-2, which fits 70 characters per text instead of 160.
_GSM = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM_SWAPS = {
    "–": "-", "—": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...",
}


def gsm_safe(text: str) -> str:
    """Plain characters only (§78.5): dashes and curly quotes swapped,
    accents outside GSM reduced to their base letter, anything else left
    out. Ñ and ñ are GSM, so Filipino names keep them."""
    import unicodedata

    out = []
    for ch in text:
        ch = _GSM_SWAPS.get(ch, ch)
        if all(c in _GSM for c in ch):
            out.append(ch)
            continue
        out.append("".join(c for c in unicodedata.normalize("NFKD", ch) if c in _GSM))
    return "".join(out)


def sms_text(ctx: NoticeContext, meeting=None, *, language: str = "FIL") -> str:
    """One language per text, chosen by the adviser; Filipino by default."""
    adviser = prose_name(ctx.adviser_name)
    first = prose_name(ctx.learner_first)
    learner = ctx.learner_prose
    who = f"{learner} ({ctx.section_label}), FGNMHS"
    if language == "EN":
        if meeting:
            text = (
                f"Good day! This is {adviser}, adviser of {who}. May we talk with you at "
                f"the school on {date_short_en(*meeting)} about {first}'s {ctx.term_name} "
                "progress? Thank you!"
            )
        else:
            text = (
                f"Good day! This is {adviser}, adviser of {who}. May we talk with you "
                f"about {first}'s {ctx.term_name} progress? Please reply or visit the "
                "school. Thank you!"
            )
    elif meeting:
        text = (
            f"Magandang araw po! Si {adviser} po ito, adviser ni {who}. Maaari po ba "
            f"namin kayong makausap sa paaralan sa {date_short_fil(*meeting)} tungkol sa "
            f"{ctx.term_name} ni {first}? Salamat po!"
        )
    else:
        text = (
            f"Magandang araw po! Si {adviser} po ito, adviser ni {who}. Maaari po ba "
            f"namin kayong makausap tungkol sa {ctx.term_name} ni {first}? Mangyari pong "
            "mag-reply o bumisita sa paaralan. Salamat po!"
        )
    return gsm_safe(text)


def sms_link(mobile_e164: str, text: str) -> str:
    """An `sms:` link that opens the phone's messaging app with the number
    and message filled in. `?&body=` is the form both Android and iPhone
    accept."""
    from urllib.parse import quote

    return f"sms:{mobile_e164}?&body={quote(text)}"


# --- Printed letter (§78.5) -----------------------------------------------------


def letter_paragraphs(ctx: NoticeContext, meeting) -> dict:
    """The letter's words; the layout is `app.concern_letter`. A meeting is
    required, since the letter names one."""
    day, at = meeting
    return {
        "salutation_en": f"Dear Parent/Guardian of {ctx.learner_upper},",
        "body_en": (
            "We would like to talk with you about your child's progress in "
            f"{ctx.term_name} of School Year {ctx.school_year_dashed} "
            f"({ctx.grade_section}). We kindly ask you to come to the school on "
            f"{date_long_en(day)} at {time_en(at)} to meet with me, the class adviser. "
            "If you cannot come at that time, please indicate a preferred schedule on "
            "the slip below."
        ),
        "salutation_fil": f"Mahal na Magulang/Tagapag-alaga ni {ctx.learner_upper},",
        "body_fil": (
            "Nais po naming makausap kayo tungkol sa progreso ng inyong anak sa "
            f"{ctx.term_name} ng Taong Panuruan {ctx.school_year_dashed} "
            f"({ctx.grade_section}). Magalang po naming hinihiling na kayo ay pumunta sa "
            f"paaralan sa {date_long_fil(day)}, {time_fil(at)} upang makausap ako, ang "
            "tagapayo ng klase. Kung hindi po kayo makararating, mangyari pong isulat sa "
            "ibabang bahagi ang nais ninyong araw at oras."
        ),
        "closing": "Respectfully / Lubos na gumagalang,",
        "signatory": ctx.adviser_name.upper(),
        "signatory_title": "Class Adviser / Tagapayo ng Klase",
        "slip_title": "ACKNOWLEDGEMENT / PATUNAY NG PAGTANGGAP",
        "slip_note": "(return to the class adviser / ibalik sa tagapayo)",
        "slip_received": (
            f"Received the letter about {ctx.learner_upper} ({ctx.section_label}), "
            f"{ctx.term_name}."
        ),
        "slip_attend": f"I will attend on {date_short_en(day, at)} / Darating ako",
        "slip_other": "I can't attend; I can come on / Hindi makararating; maaari ako sa:",
    }
