# Parent notice templates (spec §78)

Approved wording, 2026-10-04. These are the source text for steps 3 and 4
of the §78 build. Use them verbatim, filling the `{placeholders}`.
The adviser may still edit a message before it goes out (§78.5).

Decisions behind them:

- **Emails and the letter are bilingual**: English first, then Filipino.
- **An SMS is one language**, picked per text by the adviser, defaulting
  to Filipino. Both languages at once would be about three texts per
  parent.
- **No adviser mobile number** anywhere.
- **The letter is signed by the adviser only.** There is no "Noted by".
- **Meeting date and time.** Set once per section and term on the notices
  page, and changeable per learner. The letter can't be printed without
  one. The concern email and SMS include it when set and use the
  "no schedule" variant otherwise.
- **The wording is "progreso", not "pag-unlad"** (the user's choice).
- **Concern notices stay general**: no grades, subjects or counts (§78.5).

Placeholders: `{learner}` is the learner's full name in normal case for
prose and UPPERCASE where marked. `{first}` is their first name. Also
`{section}` (e.g. "11-BEZOS"), `{grade_section}` (e.g. "Grade 11 – BEZOS"),
`{term}` (e.g. "Term 1"), `{sy}` (e.g. "2026–2027"), `{adviser}` (e.g.
"Ms. Maria Santos"), `{adviser_upper}`, `{adviser_short}` (e.g.
"Ms. Santos"), `{date_long_en}` (e.g. "Monday, October 12, 2026"),
`{date_long_fil}` (e.g. "Lunes, Oktubre 12, 2026"), `{time_en}` (e.g.
"9:00 AM"), `{time_fil}` (e.g. "ika-9:00 ng umaga"), `{date_short_en}`
(e.g. "Mon, Oct 12, 9:00 AM") and `{date_short_fil}` (e.g. "Lun, Okt 12,
9:00 AM").

---

## Term card email (Release)

**From:** `{adviser} (FGNMHS Adviser)` via the school sending account.
**Reply-To:** the adviser's email. **No CC.**
**Subject:** `{term} Temporary Report Card – {LEARNER} ({section})`
**Attachment:** the term card PDF, password = birthdate as YYYYMMDD.

> Good day!
>
> Attached is the {term} temporary report card of **{learner}** of {grade_section}, School Year {sy}. The file is password-protected for your child's privacy. The password is your child's **birthdate written as YYYYMMDD**. For example, March 5, 2009 is entered as 20090305. For questions, simply reply to this email.
>
> ---
>
> Magandang araw po!
>
> Kalakip po nito ang pansamantalang report card ni **{learner}** ng {grade_section} para sa {term}, School Year {sy}. Para sa privacy ng inyong anak, may password po ang file: ang **kaarawan ng inyong anak sa anyong YYYYMMDD**. Halimbawa, kung Marso 5, 2009, ilagay ang 20090305. Kung may katanungan po, i-reply lamang ang email na ito.
>
> {adviser}
> Class Adviser / Tagapayo ng Klase, {section}
> Francisco G. Nepomuceno Memorial High School – Senior High School

---

## Concern email

**Subject:** `Request to talk about {learner}'s {term} progress / Paanyaya tungkol sa {term} ni {learner}`
No attachment.

**With a meeting schedule:**

> Good day!
>
> I would like to talk with you about the {term} progress of **{learner}** of {grade_section}. We kindly ask you to come to the school on **{date_long_en} at {time_en}**. If you cannot come at that time, please reply to this email so we can agree on another schedule.
>
> Thank you very much.
>
> ---
>
> Magandang araw po!
>
> Nais ko po sanang makausap kayo tungkol sa progreso ni **{learner}** ng {grade_section} sa {term}. Magalang po naming hinihiling na kayo ay pumunta sa paaralan sa **{date_long_fil}, {time_fil}**. Kung hindi po kayo makararating sa oras na ito, mangyari pong i-reply ang email na ito upang makapagtakda tayo ng ibang araw.
>
> Maraming salamat po.
>
> {adviser}
> Class Adviser / Tagapayo ng Klase, {section}
> Francisco G. Nepomuceno Memorial High School – Senior High School

**No schedule set.** Replace the second sentence of each half:

- EN: "Please reply to this email or visit the school at your earliest convenience so we can discuss how best to support {first}."
- FIL: "Mangyari po lamang na i-reply ang email na ito o bumisita sa paaralan sa lalong madaling panahon upang mapag-usapan natin kung paano higit na matutulungan si {first}."

Then drop the "If you cannot come…" sentence from each half.

---

## SMS (from the adviser's own phone, one language)

**Plain GSM characters only.** An en dash, a curly apostrophe or an
emoji switches the whole text to 70 characters per text. Filipino is
the default.

- **FIL, with schedule:** `Magandang araw po! Si {adviser_short} po ito, adviser ni {learner} ({section}), FGNMHS. Maaari po ba namin kayong makausap sa paaralan sa {date_short_fil} tungkol sa {term} ni {first}? Salamat po!`
- **FIL, no schedule:** `Magandang araw po! Si {adviser_short} po ito, adviser ni {learner} ({section}), FGNMHS. Maaari po ba namin kayong makausap tungkol sa {term} ni {first}? Mangyari pong mag-reply o bumisita sa paaralan. Salamat po!`
- **EN, with schedule:** `Good day! This is {adviser_short}, adviser of {learner} ({section}), FGNMHS. May we talk with you at the school on {date_short_en} about {first}'s {term} progress? Thank you!`
- **EN, no schedule:** `Good day! This is {adviser_short}, adviser of {learner} ({section}), FGNMHS. May we talk with you about {first}'s {term} progress? Please reply or visit the school. Thank you!`

---

## Printed letter (one page, schedule required)

> *[Letterhead]* {today_long_en}
>
> Dear Parent/Guardian of **{LEARNER}**,
>
> We would like to talk with you about your child's progress in **{term}** of School Year {sy} ({grade_section}). We kindly ask you to come to the school on **{date_long_en} at {time_en}** to meet with me, the class adviser. If you cannot come at that time, please indicate a preferred schedule on the slip below.
>
> Mahal na Magulang/Tagapag-alaga ni **{LEARNER}**,
>
> Nais po naming makausap kayo tungkol sa progreso ng inyong anak sa **{term}** ng School Year {sy} ({grade_section}). Magalang po naming hinihiling na kayo ay pumunta sa paaralan sa **{date_long_fil}, {time_fil}** upang makausap ako, ang tagapayo ng klase. Kung hindi po kayo makararating, mangyari pong isulat sa ibabang bahagi ang nais ninyong araw at oras.
>
> Respectfully / Lubos na gumagalang,
> **{adviser_upper}**, Class Adviser / Tagapayo ng Klase
>
> ✂ - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
>
> **ACKNOWLEDGEMENT / PATUNAY NG PAGTANGGAP** (return to the class adviser / ibalik sa tagapayo)
> Received the letter about **{LEARNER}** ({section}), {term}.
> ☐ I will attend on {date_short_en} / Darating ako
> ☐ I can't attend; I can come on / Hindi makararating; maaari ako sa: ______________
> Name / Pangalan: ______________ Signature / Lagda: __________ Date / Petsa: ________
