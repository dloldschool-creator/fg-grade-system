# FGNMHS Grading, Attendance, Forms & Awards System

## Project

Web app for Francisco G. Nepomuceno Memorial High School (FGNMHS) Senior High
School, replacing an Excel automation workbook. Grade 11 & 12, Academic +
TechPro tracks, three terms (T1/T2/T3) per school year, starting SY 2026-2027.

Full original specification: see `docs/master-spec.md` (paste the original
master prompt there — covers RBAC, all report forms, calculation rules,
72+ numbered sections in detail. This file is the working summary; that
file is the source of truth for anything ambiguous).

Real DepEd form templates with sample data live in `sf-templates/` (SF9,
SF2 so far — SF10 to be added once the school's finalized file is
available). These are the actual cell layout/formatting/logos to match
exactly when building PDF generation — not just a reference for field
names. They're currently print-view sheets pulled from a separate master
workbook via external-link formulas (`[1]SETUP!`, `[1]SUMMARY!`, etc.);
the plan is to reuse their exact layout/merges/fonts/logos but replace
those formulas with values written by our own grading engine via
`openpyxl`, then flatten to PDF with headless LibreOffice.

## Stack (decided, do not re-litigate without discussion)

- **Frontend/app:** Streamlit, multipage, role-gated
- **Database:** Supabase (Postgres) — free tier
- **Data layer:** SQLAlchemy + Alembic for ALL data access, models, and
  migrations. Connect directly to the Supabase Postgres connection string
  via a service-role connection (not through PostgREST).
- **Auth & Storage only:** supabase-py — used strictly for login/session,
  user provisioning (Supabase Auth, incl. the `auth.admin.*` service-role
  calls in `app/user_provisioning.py`), and PDF file storage (Supabase
  Storage). Never used for data queries — that's SQLAlchemy's job.
- **User provisioning uses generated temporary passwords, not Supabase's
  invite-email link.** The invite flow redirects the browser with the
  session token in the URL *fragment* (`#access_token=...`), which only a
  JavaScript frontend can read — a pure-Python Streamlit backend never
  sees it. `app/user_provisioning.py` creates accounts with
  `auth.admin.create_user()`/resets via `update_user_by_id()` instead; the
  admin relays the shown-once temporary password out-of-band, and the
  person changes it via the in-app "Change Password" control (an ordinary
  authenticated `update_user()` call, no email link involved). Don't
  switch back to `invite_user_by_email` without solving the fragment
  problem first (would need a JS bridge).
- **RBAC enforcement:** application-layer, in Python, checked against the
  `users` / `roles` / `user_roles` tables. Postgres RLS is a defense-in-depth
  backstop only, not the primary enforcement mechanism (service-role
  connections bypass RLS).
- **PDF generation:** fill official DepEd Excel templates (SF9/SF10/SF2)
  with `openpyxl` (preserves exact cell layout), then render to PDF with
  **`app/xlsx_render.py`** — pure Python/ReportLab, no external program.
  Award certificates and temp cards are drawn directly with ReportLab.
  **LibreOffice was removed** (see the Phase 14 follow-up entry): each
  `soffice` conversion peaked at 261 MB RSS with no concurrency limit, so
  three simultaneous downloads exceeded a 1 GB host, and it was the only
  reason deployment needed OS-level packages at all. `app/pdf_convert.py`
  is deleted; `requirements.txt` alone now deploys the app.
  The renderer reads the geometry the *template* carries (merges, widths,
  borders, fonts, images, page setup) rather than hardcoding any form's
  layout, so §56's DATA/PRINT-TEMPLATE separation still holds and SF10
  will render with no new layout code.

## Non-negotiable business rules

1. **All official grade calculations are deterministic, server-side.
   Never use an LLM to compute a grade.**
2. **NULL means "not yet encoded." Never default a missing grade/score to 0.**
   Blank grade ≠ zero grade, anywhere in the UI or a report.
3. **Grade 11 combined-language rule** (Effective Communication / Mabisang
   Komunikasyon): each is **encoded and stored** separately per term, but
   for averaging the two are combined into ONE virtual learning area —
   in the annual General Average (average of their two finals) **and, since
   DO 017 s. 2026, in the Term Average too**, where the pair counts once at
   one core subject's weight. It used to count twice there; spec §17 and
   NOTE 7 were amended on 2026-08-20 to match.
   On Grade 11 SF9, the two component subjects show their term grades but
   their individual Final Grade cells stay BLANK — only the combined parent
   row shows a Final Grade. The term card now prints the same shape. Modeled
   via `combined_learning_areas` / `combined_learning_area_components`
   tables, not hardcoded logic — this rule is unit-tested (see spec Section
   68, Test A) and is a major source of General Average bugs if implemented
   wrong.
4. **General Average is NOT the average of the three Term Averages.**
   It's computed from applicable subject Final Grades (which respect each
   subject's actual term-offering pattern — some electives run one term,
   some two, some all three). **Since DepEd Order 017 s. 2026 those finals
   are weighted by each subject's units, not averaged flat** — and so is
   the Term Average. See "DO 017 and the unit system" below; the rule above
   still holds, it is the *combining* step that changed.
5. **Section Subject Offerings are the single source of truth** for what a
   learner is actually graded on — subject_profiles only seed defaults.
   Grade 12 "Elective 2"/"Elective 3" labels from the old workbook are
   placeholders, never real subject names — must be resolved before an
   offering is usable.
6. **No silent recalculation of finalized years.** Policy/weight/threshold
   changes apply only to newly created school years or via explicit
   versioned policy records — never retroactively.
7. **Workflow states:** grades go DRAFT → SUBMITTED → VERIFIED → FINALIZED.
   A finalized grade is read-only except via an explicit, audited reopen.
8. **Every sensitive change is audit-logged**: who, what, old value, new
   value, timestamp, reason (where required).
9. **Optimistic concurrency** on mutable grade/attendance rows (a `version`
   column) — multiple teachers may be working concurrently; never allow a
   silent overwrite.
10. **LRN is stored as text**, never numeric (preserves leading zeros),
    validated as 12 digits, unique, never placed in a URL.
11. **Grade entry is direct-only (Mode B).** Subject teachers type one
    official term grade per learner/subject/term — no in-app assessment-level
    (Written Work/Performance Task/Exam) breakdown or auto-transmutation for
    this phase; that computation happens in the teacher's own external
    tools before they encode the final number. Matches how the current
    Excel workbook actually operates. Do not re-add assessment-level entry
    without discussion.

## DO 017 and the unit system

**DepEd Order 017 s. 2026 (Strengthened SHS Curriculum), signed 4 June
2026, effective SY 2026-2027.** Read Annex E before touching anything
that averages. The PDF is a 100-page scan with no text layer; the parts
that matter are reproduced as executable fixtures in
`tests/test_do17_unit_system.py`, which is the fastest way to see what
the order actually requires.

**What changed.** The Term Average and the General Average are now
**unit-weighted**: `Σ(grade × units) ÷ Σ(units)`, rounded half-up to a
whole number. Table 19 sets the units — core 2 per term, academic
elective 3, arts elective 6, TechPro elective 4 in Grade 11 and **12** in
Grade 12, work immersion 12. Annual units are units-per-term × the terms
the subject actually ran, so a three-term core is 6 and a one-term
elective is 3.

It matters most where units differ inside one average. On DepEd's own
Grade 12 cross-track example — eight 3-unit electives and one 12-unit
TechPro elective — the flat mean is **87** and the weighted answer is
**89**. That is the shape the school's Grade 12 TechPro sections have.

**Both grade levels are SSHS this year, because FGNMHS is a pilot
school.** DO 017 ¶7 phases SSHS in by grade level — Grade 11 in
SY 2026-2027, Grade 12 in SY 2027-2028 — but the ¶7 exemption keeping
Grade 12 on the 2016 curriculum applies only to learners *not enrolled in
pilot schools*, and this school piloted under DepEd Memorandum 048 s.
2025 (confirmed 2026-08-20). So Grade 12 is unit-weighted here too, and
its TechPro electives carry **12 units per term**, the heaviest weight in
Table 19.

The per-grade-level machinery still exists and should stay:
`grading_policy_versions.effective_grade_level_id` is what would let two
curricula coexist, which is the ordinary case for a non-pilot school and
for any future phased change. Never write `if grade_level == "G11"`; the
rules are resolved from a versioned policy row by
`app/curriculum_policy.py`, and at equal specificity the **highest
version number wins**, which is how DO 017's version supersedes the
unweighted baseline without the baseline being deleted.

**Where the pieces live:**

- `app/grading_engine.py` — the arithmetic, still dependency-free.
  `weighted_average` is the **only** averaging implementation; unweighted
  is the same function with every unit forced to 1, so the two policies
  cannot drift apart. `AveragingMethod` is defined here and re-exported by
  `app/models/enums.py` — the arrow points that way so importing the
  engine can never pull in `app.models` and disturb import order.
- `app/curriculum_policy.py` — which rules apply, and the unit resolution
  chain: offering → subject → category → 1. The fallback is **1, never
  0**; an unconfigured subject must keep counting once, not vanish from
  the average.
- `grading_policy_versions` — the switches, all defaulting to pre-DO-017
  behaviour, scoped by `effective_school_year_id` and the new
  `effective_grade_level_id`. Most specific version wins.
- `scripts/apply_do17_units.py` — writes the units and activates the
  policy. Dry-run by default; stages its changes and rolls back, so the
  dry run predicts rather than describes the state it replaces.
- `app/admin_pages/subject_units.py` — **Setup → Subject Units**, where
  the values are read and edited without SQL. Shows the whole chain and
  each subject's effective weight, refuses 0 (blank is how you inherit),
  audit-logs every change as `SUBJECT_UNITS_CHANGED`, and says plainly
  that editing does not rebuild existing averages.

**Two things DO 017 does not settle on its own**, both stored as switches
rather than decided in code, and both now answered by the school:

1. **`combine_language_pair_in_term_average` — True** (decided
   2026-08-20). Table 1 makes Effective Communication / Mabisang
   Komunikasyon a single 160-hour core subject, so the pair is counted
   **once, at one core subject's weight (2 units)** — not 2 + 2. This
   overrides master-spec §17's "count the two components separately" for
   the Term Average. **§17 and NOTE 7 were amended to match on 2026-08-20**
   (with approval); §17 now also carries the unit formula, and a new §17A
   holds Table 19, the annual-units rule and the pilot-school
   applicability. **§19, §20 and §61 were amended the same day**, so the
   spec now carries the weighted formula throughout. §14, §15 and §18 were
   deliberately left alone — they average a subject's *own* terms, which
   units never touch.
2. **`average_from_unrounded_finals` — True.** Annex E's arithmetic
   weights 78.66… where its own table prints 78; and the same subject
   prints as **78 on p. 84 and 79 on p. 86** of that annex. True is the
   setting that reproduces DepEd's own totals.

**Display and computation moved separately, and that is deliberate.** The
pair still *prints* as §16 always printed it — parent row carrying the
grade that counts, two components indented beneath for information — on
the report card **and now on the term card too**, which used to list the
two components flat. Only the arithmetic changed. The invariant to hold
onto: whatever the switch says, `build_term_subject_rows` and the Term
Average must read it the same way, or the card prints a list that doesn't
add up to the number under it.

**What DO 017 does NOT change**, despite touching the same area: the
passing mark, retention, promotion, graduation and honors. §25 and §26
defer all of those to a forthcoming "policy on assessment, grading
system, and awards and recognition". Don't move `passing_grade` or the
award tiers on the strength of this order.

One rule from it that isn't about arithmetic: a learner who enrols in
**more** electives than the required minimum must pass **all** of them to
graduate (p. 15). Not yet reflected in the finalize guard.

## The deployed host runs a different Python from local dev

**Host: Python 3.14. This machine: 3.13.** That gap took the live app
down on 2026-08-12 and everything had passed locally first, so treat
"it imports fine here" as weak evidence about the host.

What happened: `app/admin_pages/_helpers.py` gained
`from app.models.academic_structure import ...` at module load. Every
page imports `_helpers`, so it became the **first** thing to initialise
`app.models` — and `app/models/__init__.py` imports its own submodules
while still initialising itself. On 3.14 that re-entry executed a model
module twice, and the second `class GradeLevel(...)` raised
`InvalidRequestError: Table 'grade_levels' is already defined for this
MetaData instance`. Nothing rendered; the app was dead on every request.

Two rules that came out of it:

1. **`_helpers.py` must not import `app.models` at module load.** It is
   imported by every page, so anything it imports at load time dictates
   the whole app's import order. `section_picker` imports its models
   *inside the function* for exactly this reason — don't "tidy" that back
   to the top.
2. **`server.fileWatcherType = "none"`** in `.streamlit/config.toml`. The
   watcher re-imports local modules it believes have changed, and
   re-executing a model module produces the same error. Deployed files
   never change while running, so the watcher is pure risk there. The
   cost is local only: restart Streamlit after editing a module instead
   of relying on hot reload.

The lasting fix is to **pin the host to the same Python version as
local** (Streamlit Cloud takes a Python version in its advanced deploy
settings). Until that is done, an import-order change is a deployment
risk that local tests cannot catch.

## If DepEd reverts to four quarters

Asked 2026-08-12; audited then, so trust this map over a fresh guess.
**Less likely since DO 017 s. 2026** (2026-08-20): the Strengthened SHS
Curriculum is built on terms, not quarters — 160 hours across 3 terms per
core subject — and Annex D2 ships an official *"Sample Three-Term Class
Program"* for the outgoing Grade 12 cohort. The school's three-term
structure is now the nationally illustrated one. The map below still
stands if it happens.
**The core is already period-agnostic; four places bake "three" into
structure rather than logic.**

Already fine, no change needed:

- `terms` is a **table**, not a constant — a school year takes as many
  term rows as you give it.
- `compute_subject_final_grade(term_grades, required_terms)` and the
  Term/General Average take the term set as a **parameter**, sourced from
  each subject's real `section_subject_offerings`. That is why electives
  running one or two terms already work; a fourth flows the same path.
- Attendance, the audit trail, RBAC, and the permanent academic record
  never count terms.
- **SF2 and SF4 are unaffected** — they report a *month*. Same reasoning
  that let SF4 be built while SF5 waits (§77.1).

Would need work — the four touchpoints:

1. **Four tables have physical `term1/2/3` columns**: `enrollments`
   (`termN_adviser_comment`), `subject_profile_subjects`
   (`termN_active`), `combined_learning_area_results` (`termN_combined`),
   `learner_academic_record_subjects` (`offered_termN`, `termN_grade`).
   Adding `term4_*` alongside is an **additive** migration, so it deploys
   without downtime (see `docs/operations.md`).
2. **`sf9_report.COL_TERM = {1: 8, 2: 9, 3: 10}`** — the official SF9 has
   three term columns. A four-quarter form is a *different DepEd
   template*, not a config change.
3. **`sf9_report.TERM_FLAG_PLACE = {1: 100, 2: 10, 3: 1}`** — the
   block-out helper is a 3-digit code (§35 notes); a fourth term has
   nowhere to go in it.
4. **UI laid out as three**: the adviser-comment boxes on Enrollment, and
   the term card.

**Scope: days, not a rewrite** — and note that a reversion ships new
official SF9/SF10 templates anyway, so item 2 is work the reversion
creates rather than overhead this design imposes. Historical three-term
years stay correct because `learner_academic_records` freezes them as
**text, not references** (§38) — designed for subject renames, but it
protects against this too.

## Traps already hit

Every one of these produced *plausible wrong output* rather than an error,
which is why they're here and not left to be rediscovered. The full story
of each is in `docs/build-log.md`.

**Data and correctness**

- **`server_default="now()"` as a string freezes the timestamp.** Postgres
  resolves `DEFAULT 'now()'` once, at migration time, so fourteen columns
  shared one identical creation time. Always `server_default=func.now()`.
  `tests/test_model_defaults.py` fails on any `server_default` string
  containing `(`.
- **No ORM `relationship()` exists anywhere in this codebase**, by design.
  SQLAlchemy therefore can't order a new parent row before a new dependent
  row that references it by a bare FK column. Any page creating two related
  rows in one action must call `_helpers.flush_or_rollback(session)` on the
  parent first.
- **Never a bare `session.commit()`** where a unique/FK constraint could
  fire — use `_helpers.try_commit()` / `try_delete()`, or an
  `IntegrityError` crashes the whole page instead of showing a message.
- **Don't call a `get_or_create_*` helper on a page's plain view path.** It
  INSERTs on every render, leaving an uncommitted insert open and racing
  concurrent users into a unique violation. Split read-only `get_*` from
  `get_or_create_*`.
- **Percentages must never be summed.** A M/F/Total row that defaults Total
  to M+F is right for counts and daily averages and reports 200% for a
  percentage. Bit SF2 and again SF4 — pass an explicitly recomputed total.
- **Excel destroys a 12-digit LRN on CSV export** (`107041140016` →
  `1.07041E+11`), unrecoverably, and two different learners can round to the
  same value — so accepting the expansion would manufacture duplicates that
  don't exist. `.xlsx` is unaffected. `parse_lrn`'s significant-digit guard
  is what enforces this; don't loosen it.
- **`VersionMixin` is not universal.** Don't copy `.version += 1` onto a
  model without checking `docs/schema.md` that it has the column.
- **`subject_profiles` has no section column**, so every section in a
  strand matches the same set of profiles — the four Kitchen Operations
  sections each list all four. The only thing telling them apart is the
  naming convention `G12-TECHPRO-KO-<SECTION>`, a string rather than a
  foreign key. Section Subject Offerings listed them unordered with no
  `index=`, so the picker defaulted to whatever row Postgres returned
  first: selecting MUSK showed `G12-TECHPRO-CSS-JOBS` while the offerings
  list underneath, queried by `section_id`, was correct. Reported from the
  live app on 2026-08-20 — nine sections exposed, six of them defaulting to
  another section's profile, and **nothing had been mis-seeded yet**.
  Not cosmetic: profiles in one strand differ by subject *and by term*. The
  two CSS profiles run the same three subjects in swapped terms, and the
  Kitchen Operations ones differ in the subject itself (Kitchen Operations
  vs a 12-unit Work Immersion). Seeding from the wrong profile is silent,
  and rule 4 builds the General Average from each subject's real term
  pattern. `profile_for_section` now resolves it — strict exact-suffix,
  exactly one hit — and when the convention can't answer, the picker
  preselects **nothing** and both buttons refuse, rather than defaulting to
  a stranger's profile. `tests/test_section_profile_default.py` covers the
  matching and asserts that no live section is left unresolvable, so
  renaming a section into ambiguity fails a test instead of a report card.
  The durable fix is a real `section_id` on `subject_profiles`; that is a
  mid-year migration and the convention currently holds for every section.
- **Re-categorising a subject does not re-weight its existing offerings.**
  `section_subject_offerings.subject_category_id` is a *snapshot* taken when
  the offering is created — deliberately, because §48 makes the offering the
  source of truth and a section may confirm a category the catalog no longer
  has. So `curriculum_policy.load_offering_units` reads the **offering's**
  category, while Setup → Subject Units reads the **subject's**. Change a
  subject's category in Subject Catalog and the two disagree silently: the
  page shows the new units, the grading engine keeps using the old ones.
  Hit on 2026-08-20 splitting the Grade 11 TechPro electives into their own
  4-unit category — all 30 offerings stayed on `TECHPRO_ELECTIVE`, which had
  just been set to 12, so Grade 11 was weighted at 12 while the screen said
  4. On a 6-core Grade 11 TechPro section that made the TechPro grade 55% of
  the term average instead of 29%. **Units themselves are never snapshotted**
  — editing a category's or a subject's `units_per_term` takes effect
  immediately, with nothing to resync. Only the category assignment is.
  **Subject Catalog now closes the hole at the source**: a subject holding
  offerings shows a ticked-by-default "also move its N existing offering(s)"
  box, and saving a category change with it *un*ticked flashes a warning
  naming the count rather than going quietly. Untick it only for a genuine
  per-section override. `_recategorise_offerings` does the move, audited per
  offering as `SUBJECT_OFFERING_CHANGED` and bumping each `version`.
  `scripts/resync_offering_categories.py` is the same repair from the
  command line, for offerings stranded before that existed (targeted, never
  a blanket resync — a mismatch can be a legitimate per-section override).
  `tests/test_subject_recategorisation.py` asserts the trap itself, so the
  engine reading the offering's category can't be "tidied" away.
  The CSV importer is not affected: it only INSERTs subjects whose code is
  new, so it can never strand an existing offering.
- **Prefer a subject-level unit override to a split category** when DO 017
  gives one category two values. Table 19 gives a TechPro elective 4 units
  in Grade 11 and 12 in Grade 12, and both are `TECHPRO_ELECTIVE`; that is
  what `subjects.units_per_term` is for and what `apply_do17_units.py`
  writes. A split category is the tempting alternative and is worse three
  ways: it hits the snapshot trap above, it permanently diverges the
  frozen academic record (`academic_record_service` freezes the category
  *name* as text, §38), and it fails **silently**. The override fails
  loudly — forget it on a new Grade 11 TechPro subject and Subject Units
  shows the inherited 12 in that row, because the page reads the same
  resolution chain the engine does. Reverted on 2026-08-20 by
  `scripts/fix_g11_techpro_units.py`, and the `TECHPRO_ELECTIVE_3_TERMS`
  category deleted once empty. Also note the name's premise was wrong —
  Table 19 splits by **grade level**, not term count, and the engine
  already multiplies units-per-term by the terms a subject actually ran, so
  folding term count into a unit label double-counts it.
- **`AuthUser.id` is a `str`; every `*_user_id` column is a `uuid.UUID`.**
  Postgres coerces between them, so `filter_by(adviser_user_id=current_user.id)`
  works and hides the mismatch — but the same two values compared **in
  Python** are never equal. `section.adviser_user_id == current_user.id` is
  always False. It shipped in the adviser bulk-enrol check on 2026-08-17
  and told an adviser her own section wasn't hers, while the panel right
  above it (a SQL query) listed that section correctly. Use
  **`app.section_access.is_advised_by(section, user_id)`** — the one
  implementation, deliberately dependency-free so importing it can never
  affect import order — and **pass `str(user.id)` in tests**, since passing
  the ORM's UUID is what let four tests miss it.
- **Never order a roster on `Learner.sex` directly.** The stored strings
  are `"MALE"` and `"FEMALE"`, so alphabetical order puts FEMALE first —
  the opposite of what every DepEd form and the teachers' workbook use.
  `roster_for_month` had this bug while its own docstring claimed
  male-first. All seven rosters (Gradebook, Grade Summary, Attendance,
  SF2, SF9, Term Cards, Awards) go through `app/roster_order.py`; use
  `learner_order_by()` in a query or `learner_sort_key()` on a list,
  never a hand-rolled ORDER BY.
- **A `.join(Learner, ...)` added only to sort a query does not load the
  Learner objects.** A later `session.get(Learner, ...)` in the render
  loop is therefore a real round trip per learner — ~40 × 85ms on every
  Streamlit rerun, which is every interaction. Gradebook and SF9 both had
  this; batch with `Learner.id.in_(...)` into a dict instead.
- **Alembic enums, two gotchas:** autogenerate emits a bare `sa.Enum` inside
  `create_table` for a type that already exists (use
  `postgresql.ENUM(..., create_type=False)`); and `op.add_column` does not
  auto-create a new enum type (call `.create(op.get_bind(), checkfirst=True)`
  first).

**The §16 vs §17 language rule — the biggest source of grade bugs**

The Grade 11 language pair (Effective Communication / Mabisang
Komunikasyon) is treated in **exactly opposite ways** depending on the
form, and both are correct:

- **General Average and the SF9 report card (§16):** the pair collapses into
  ONE combined learning area. Component rows print their term grades but
  their Final Grade cells stay **blank**.
- **Term Average and the term card (§17):** the pair counts as **TWO
  separate subjects**. §17 says outright not to substitute the combined
  grade, and the term card's printed list must add up to the average beneath
  it.

`app/report_card.py` is the single implementation of both so the screen and
the printed form can't disagree. Don't reimplement either rule in a page.

**DO 017 s. 2026 overrode §17's half, and the school adopted it**
(2026-08-20): Table 1 makes the pair one 160-hour core subject, so the Term
Average counts it **once at 2 units**, and the term card prints it as a
parent with two indented components instead of two flat rows. So the two
sides of this trap now agree — the pair is one learning area in both
figures — and §17 and NOTE 7 were amended on 2026-08-20 to say so, NOTE 7
having previously recorded the divergence as deliberate.

What has *not* changed is why this section exists: the collapse is a
display rule and a weighting rule at once, and both must come from
`grading_policy_versions.combine_language_pair_in_term_average`. If
`build_term_subject_rows` and the Term Average ever read it differently,
the card prints a subject list that doesn't add up to the number under it.
Under weighting the parent carries **one** core subject's units (2), never
the components' sum, or the languages are weighted twice.

**Excel templates and PDF rendering** (`app/excel_template.py` carries these)

- **Merge anchors differ row by row** — writing to a non-anchor merged cell
  raises. All writes go through `write()`/`write_ref()`, never
  `ws.cell(...).value =`.
- **`copy_worksheet` silently drops images**, and a loaded image's bytes can
  only be read **once** — capture the bytes and build a fresh `Image` per
  sheet.
- **Check `worksheet.conditional_formatting` before painting any fill.** SF9
  blocks out non-offered terms via its own CF driven by helper column N (a
  3-digit per-term flag: 111 = all three terms). Blanking N greys out every
  grade on the card.
- **Never `PatternFill(fill_type=None)` to clear a fill** — it serialises to
  OOXML fill index 1, which is always gray125.
- **openpyxl cannot round-trip an `externalLinks` part.** Every template is
  a print-view of the school's master workbook, so every one carries a link
  to it. The templates are valid; on save openpyxl writes `externalBook
  r:id="rId1"` while numbering the surviving relationship `rId3`, and Excel
  follows the dangling id and offers to *recover* the file. **Every builder
  must `workbook._external_links = []` then `assert_no_external_links()`
  before returning** — SF2 and SF9 did, SF4 was added later and didn't, and
  it shipped a file that downloaded fine, unzipped fine, parsed fine and
  would not open. `tests/test_workbook_package.py` checks the shipped bytes
  of all three for that and for any other dangling relationship.
- **Column widths are `<col min= max= width=>` *ranges*,** keyed by the
  first letter only; reading them per letter silently leaves columns at the
  default width.
- **`fitToWidth`/`fitToHeight` are page counts, and 0 means "as many as
  needed".** Test page fitting with a full roster, never the seeded
  3-learner section.

**Streamlit**

- `st.success()`/`st.error()` immediately before `st.rerun()` never reach
  the browser — use `_helpers.flash()` / `render_flashes()`. `render_flashes`
  also toasts each message: every add form sits *below* the list it adds to,
  so the top of the page — where the inline message renders — is scrolled
  off by the time the button is pressed.
- **`st.file_uploader` keeps its file across reruns, so an import that
  reruns re-imports its own output.** The panel re-reads the retained file
  and validates it against the rows it has just written — every LRN now
  exists, so a wholly successful import of 26 learners reported "26 row(s)
  need fixing — duplicate LRN" while all 26 sat in the database. Reported
  from the live app on 2026-08-20; **all four upload panels had it**
  (Learner Masterlist, Import from Excel, Subject Catalog, Users). The fix
  is `_helpers.generation_key()` for the uploader's key plus
  `clear_text_fields(form)` on the **success branch only** — a failed
  import must keep the file, because the fix is to re-read the errors
  against it. `tests/test_add_form_reset.py` enforces it across every page
  that has an uploader, because `key="learner_csv"` reads perfectly.
- **Clearing a widget inside `st.form` needs a new key, not an empty
  value** — and the wrong version passes its tests. A form widget keeps a
  copy of its value in the *frontend*, which survives the rerun and is
  re-submitted, so deleting its `session_state` key empties the server's
  copy while the box on screen still shows the old text. That shipped, on
  Sections. `_helpers.clear_text_fields()` therefore bumps a per-form
  generation, and `_helpers.text_field()` builds keys like
  `add_section.name#3`: a key Streamlit has never issued has nothing to
  restore, browser included. Nothing is deleted, so a tick box or picker in
  the same form can't be caught by it (`clear_on_submit=True` would reset
  those too, and the next row usually wants them).
- **`AppTest` has no browser, and that gap is not theoretical.** It is the
  right tool for widget lifecycle without a login (`tests/test_add_form_reset.py`
  is the only test here driving the real Streamlit runtime), but it only
  ever shows the *server's* side. The clearing bug above passed every
  assertion in that file. Anything about what a widget displays after a
  rerun needs a real browser: run one page against
  `.claude/launch.json` and read the DOM.
- **The file watcher is off (`server.fileWatcherType = "none"`), so a local
  Streamlit keeps running the module it already imported.** Editing a
  helper and reloading the page proves nothing — restart the server, or
  you will be testing the old code and believing the new code failed.
- `st.tabs()` resets to the first tab on every rerun — use
  `_helpers.stateful_tabs()`.
- A `SelectboxColumn` cell whose value isn't in `options` renders *empty*,
  which reads as un-encoded. Sentinels must be valid options.
- **`_helpers.py` must not import `app.models` at module load** — every page
  imports it, so it dictates the whole app's import order. See the Python
  version section above.

**Performance — the database is ~85ms away, so query *count* is the cost**

- A page's cost is how many round trips it makes, not how much it returns,
  and Streamlit re-runs the whole script on every widget interaction — so
  the cost is per *click*.
- **Batch per-roster fetches into one `IN (...)`.** `report_card.load_report_context()`
  and `sf9_report.load_sf9_context()` exist for this; a preloaded context
  must issue **zero** queries while rendering.
- Load an importer's reference data **once per file**, never per row.
- **`st.download_button(data=...)` evaluates its data on every script
  run**, so an ungated one renders the whole document each time any widget
  on the page moves, for a download nobody asked for. Term Cards did this
  with a full section's PDF. Put anything that scales with the roster
  behind a Build button first, the way SF9's batch print does.
- **Streamlit runs an `st.expander()` body whether or not it is open.** A
  collapsed panel is not a skipped one. Awards generated a certificate PDF
  per eligible learner inside a collapsed expander, on every rerun — and an
  audit found the same shape on six more pages: Grade Summary (120 queries
  ≈ 10.2s per click at 40 learners), Enrollment (80 ≈ 6.8s), Learners (40),
  Users (40 at full staffing), plus Subject Profiles, Award Policy and
  School Years. All now batch above the loop.
  **`tests/test_expander_cost.py` fails on any new one** — it walks the AST
  for `for` loops containing an `st.expander`, following one level into
  module-local helpers, and allows document generation only behind a
  button. Nothing about this bug looks wrong in the source, which is why
  it is tested structurally rather than left to review.
- **Awards was the worst page in the app** — two loops each resolving the
  learner, the award row and the grade summary one at a time: ~160 queries
  and 13.6s of round trips per interaction on a 40-learner section. Now
  `_load_award_context()` does it in **3 queries, flat**. If you add a
  per-learner panel anywhere, load its data above the loop.
- `tests/test_query_cost.py` asserts this shape and stays meaningful on a
  fast local database, where the bug is otherwise invisible.

## Where things stand

**The app is live** at https://fgnmhs-shs.streamlit.app, deployed
2026-08-12 to Streamlit Community Cloud from GitHub (branch `master`,
entrypoint `streamlit_app.py`, secrets in the app's Settings panel).

**Treat every change as a live change.** Migrations must follow the
ordering in `docs/operations.md`, and a restart signs everyone out and
loses unsaved grade entry, so deploy outside encoding hours.

**But `git push` does not deploy by itself** — it syncs files, and the
running process keeps the old modules because `fileWatcherType` is off.
Someone has to hit **Reboot** in the Streamlit Cloud dashboard. The page
footer is the check: `c19e19c · up 34m · ⚠ 41faa98 is on disk — restart to
load it` means the push landed and the deploy has not. **`_stcore/health`
returns `ok` regardless**, so a green health check after a push says
nothing about whether the new code is running — read the footer instead
(`app/version.py` builds it for exactly this reason).

Full phase-by-phase build history, including the reasoning behind every
decision above: see `docs/build-log.md` (not auto-loaded — read it when you
need the history).

**Remaining before real use:** migrating the rest of the ~1,200 learners,
filling in the empty subject profiles (the Grade 12 ones are the blocker
— see the dress rehearsal below), and an end-to-end dress rehearsal on
one section.
**Done since:** 43 teacher accounts exist, and **every Grade 11 offering
carries a teacher** as of 2026-08-17 — 315 assignments written by
`scripts/import_teacher_assignments.py`, all 16 sections, 362 active
assignments in total. Grade 12's are still partial. Advisers can also
bulk-enrol their own class from the Learner Masterlist upload.
**Term 1 closes 15 September 2026.**

## Dress rehearsal, 2026-08-13 — what it found

One full pass on BEZOS (G11 BE) with six throwaway learners: import →
enrol → encode all three terms → submit → finalize → SF9, term cards,
SF2, SF4, awards and certificates → purge. Chosen over JOBS because it is
the only fully-configured section: 9 subjects / 21 offerings, both
combined-language components, and a mix of three-term cores and one-term
electives, so it exercises §16, §17 and rule 4 at once.

**Verified working, end to end:** the §16 SF9 rule (parent row carries the
Final Grade, both component rows print term grades with blank finals),
the §17 term card (pair listed as two subjects, printed list agrees with
the average beneath it), rule 2 (a missing grade stays NULL and the
learner reads INCOMPLETE, never 0), rule 4 (finals respect each subject's
own term pattern), the finalize guard (an incomplete learner is blocked,
and completing the grade unblocks them), §38's frozen record (subject
names as text, component finals still blank, offered-term flags kept),
award tiers and the not-eligible reasons, and every generated document.

**Two real defects, both now fixed or recorded:**

1. **The term-grade import never worked.** `commit_term_grades` passed
   `encoded_by_user_id=` to `TermGrade` — that column is on
   `AttendanceRecord`. SQLAlchemy raises TypeError for an unknown keyword,
   so every INSERT crashed; the UPDATE branch set it as a plain instance
   attribute, which Python allows and the ORM discards, so re-importing an
   existing grade silently appeared to work. Fixed.
   `tests/test_model_kwargs.py` now AST-checks every model constructor
   keyword in the writer modules against the real columns.
2. **The VERIFIED workflow state does not exist in the app.** Rule 7 says
   DRAFT → SUBMITTED → VERIFIED → FINALIZED. Nothing anywhere assigns
   `GradeWorkflowStatus.VERIFIED`, `verified_by_user_id` or `verified_at`,
   and there is no `GRADE_VERIFIED` audit action — although the Gradebook
   already treats VERIFIED as locked and the columns exist. Finalize also
   only checks that the annual record is COMPLETE, so a **DRAFT** grade
   can be finalized without ever being submitted. Not fixed: who verifies,
   on which page, and at what granularity is a decision, not a bug fix.

**The Grade 12 curriculum is the real blocker for real use.** As the
rehearsal found it (2026-08-13): six of the eight G12 subject profiles
held zero subjects, and the two CSS ones three each — so JOBS, the only
section with real learners, offered three subjects total. Nothing is
wrong with the code; the data was not entered yet.

**Where that stands on 2026-08-17** — checked against the database, so
trust this over the paragraph above:

- **No G12 profile is empty any more.** There are twelve of them, each
  holding 1–4 subjects. Grade 11's five are complete (7–9 subjects each),
  not just BE.
- **The gap is now terms, not subjects.** Outside `G12-TECHPRO-CSS-JOBS`
  and `-MUSK`, every G12 subject sits in **Term 1 only** — DESCARTES,
  FREUD, LOCKE, MASLOW and SMITH have four subjects in T1 and *nothing*
  in T2 and T3, and the Home Economics sections carry one subject for the
  year. A General Average can't be computed from that (rule 4 reads each
  subject's real term pattern), so this is what to fill in first.
- **G12 teaching assignments are all but done**: 32 of the 33 existing
  offerings carry a teacher. The only one that doesn't is DARWIN /
  Tourism Services / T1.
- Grade 11 is fully assigned — see the build-log entry for
  `scripts/import_teacher_assignments.py`, which is the tool to reuse
  once the T2/T3 offerings exist.

### Still open

- [x] **DO 017's unit system is live** (2026-08-20). Migration
      `c3f1a7d90b42` applied, code deployed at `184a658`, and
      `scripts/apply_do17_units.py --confirm` run: 5 category unit
      defaults, 6 TechPro subject overrides, the language pair at 2 units,
      and an ACTIVE policy version v2 (all grade levels, SY 2026-2027,
      UNIT_WEIGHTED, unrounded finals, pair combined). Every one of the
      369 offerings resolves to a real unit value — none falls back to 1.
      **No recompute was needed**: `term_grades` was empty when it was
      switched on, so there is no split between learners graded under the
      old rule and the new one. Everything encoded from here is weighted.
- [ ] **`docs/master-spec.md` §68 has no required test for the unit
      system.** `tests/test_do17_unit_system.py` reproduces all seven
      Annex E tables, but §68's list of required tests predates it. A
      "Test G" entry would close the gap. Not done — §68 wasn't in the
      approved amendment scope.
- [ ] Grade 12's T2/T3 offerings are still the blocker for a Grade 12
      General Average, and units make it sharper: a G12 TechPro elective
      is 12 units per term, so a section offering one subject in T1 and
      nothing after has an annual average built from a single 12-unit
      entry.

- [ ] Blocked, needs you: drop the school's SF10 file into
      `sf-templates/` and the report layer can be built on top of the
      record — `app/excel_template.py` already carries the five
      openpyxl/Excel traps SF2 and SF9 hit. Label the output
      **TEMPORARY THREE-TERM SF10 – FOR SCHOOL USE ONLY** (§36.3).
- [ ] Also still open: §37's Grade 11 prior-entry/eligibility fields
      (PEPT, ALS A&E, CLC, previous school) already exist in
      `learner_admission_records` and are deliberately NOT snapshotted —
      they describe a single admission event rather than a year's result.
      Revisit if the SF10 layout needs them frozen too.
