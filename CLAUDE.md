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

1. **If you add a module-level import to a module most pages import, it
   must not reach `app.models`** — directly or through what it imports.
   The rule is about the class of file, not the filename: `_helpers.py`
   is the one that broke, but anything universally imported decides when
   `app.models` first initialises, and the next such helper won't be
   called `_helpers`. Import it *inside* the function instead;
   `section_picker` in `_helpers.py` is the worked example, so don't
   "tidy" that back to the top. Otherwise a module-level `app.models`
   import is perfectly fine — most service modules have one, and 48
   modules do it today.
   **`tests/test_import_order.py` enforces this.** It derives the
   governed set from the pages rather than carrying a list, so a new
   shared helper is covered the day it becomes one, and it walks each
   module's load-time closure — `_helpers` importing something that
   imports `app.models` is the same outage.

   **`app/auth.py` was the second instance, and it is closed** (2026-08-21).
   It is imported at module load by all 29 pages and the entrypoint — one
   more than `_helpers`, which the entrypoint does not import — and it did
   `from app.models.rbac import ...` at module level, the same shape as the
   change that killed the app. It escaped being *first* only because isort
   sorts `app.admin_pages._helpers` above `app.auth`, putting `_helpers` at
   line 5 of every page and `auth` at line 18. It carried that import
   through the whole 3.14 period without crashing, which is evidence but
   not an explanation — it never said why `app.models.rbac` was survivable
   where `app.models.academic_structure` was not. So rather than exempt it
   on "hasn't crashed yet", the three names moved into the two functions
   that use them: `_load_or_provision_user` and `_record_password_change`,
   both once per sign-in, neither on a rerun path. `KNOWN_EXCEPTIONS` in
   the test is empty now and should stay that way.

   **Verifying a change to `app/auth.py` needs more than the suite.**
   `tests/test_password_gate.py` covers both functions by
   `inspect.getsource()` substring assertions — it reads them, it never
   runs them, so the suite goes green whether or not sign-in still works.
   The move was checked three ways instead: importing `app.auth` first in
   a fresh interpreter and asserting `app.models` is *not* in
   `sys.modules` afterwards (the property the whole rule is about);
   calling both functions with `auth.SessionLocal` stubbed to raise, which
   proves the function-level import resolves at call time without touching
   the database; and booting the app to confirm the login page renders.
   An end-to-end sign-in is still a human step.

2. **`server.fileWatcherType = "none"`** in `.streamlit/config.toml`. The
   watcher re-imports local modules it believes have changed, and
   re-executing a model module produces the same error. Deployed files
   never change while running, so the watcher is pure risk there. The
   cost is local only: restart Streamlit after editing a module instead
   of relying on hot reload.

The lasting fix is still to **pin the host to the same Python version as
local**. Streamlit Cloud does not read `.python-version` or the
devcontainer — it has its own setting (Manage app → Settings → Python
version), it has to be changed by hand, and it cannot be asserted from
this repo; `docs/deployment.md` carries the procedure and the way to
check what the host is actually running.

What changed on 2026-08-21 is that this particular bug is no longer one
only the host can find: `tests/test_import_order.py` fails on the
offending import shape whatever interpreter runs it. A version gap is
still a deployment risk for everything else, so "it imports fine here"
remains weak evidence about the host — but not for this.

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
- **Almost no ORM `relationship()` exists, and the few that do are not
  used for inserts** — so SQLAlchemy can't order a new parent row before
  a new dependent row that references it by a bare FK column. Any page
  creating two related rows in one action must call
  `_helpers.flush_or_rollback(session)` on the parent first.
  **The tempting wrong inference:** this used to be written as "no
  `relationship()` exists anywhere, by design", which was never true —
  six have existed since the initial commit (`School.school_years`,
  `SchoolYear.school`/`.terms`, `Term.school_year`, `User.user_roles`,
  `UserRole.user`). A reader who spots `SchoolYear.terms` and concludes
  the ordering is handled for school years is wrong twice over: the
  School Years page builds its terms as `Term(school_year_id=new_sy.id,
  ...)`, a bare FK column the relationship never sees, and it is one of
  the two places the bug was actually hit. A `relationship()` only helps
  the unit-of-work when you assign *through* it (`sy.terms.append(...)`),
  which nothing here does. So the rule is about how the row is written,
  not about which models happen to declare a relationship — check the
  insert, never the model.
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
- **Scoping that is present is not the same as scoping that is enough.**
  The Learner Masterlist carried `adviser_user_id` from the day it was
  written — and used it only to decide which *section* the add form and
  the bulk panel could enrol into. The list of people was never scoped, so
  every adviser could search all ~1,200 learners and retype any name,
  birthdate or LRN, with Delete alongside. §3C and §54 had said otherwise
  the whole time. Found by inspection on 2026-08-21, not by a report,
  because nothing on the page looks wrong. **When a page takes
  `adviser_user_id`, check what it actually filters** — every other
  adviser page reaches its learners through `section_picker`, and this one
  was the outlier because it is not organised around a section.
  The rule now lives in `app/learner_access.py` and has **two halves**:
  learners enrolled in a section you advise, *plus* learners you created
  who are enrolled nowhere. Dropping the second breaks the bulk panel,
  which deliberately refuses a Section the uploader doesn't advise and
  creates the learners anyway — they land in no section, and would belong
  to nobody. `learners.created_by_user_id` answers that; NULL means
  registrar-only, never everyone. A stranger's learner still renders, as a
  read-only card: `lrn` is uniquely indexed, so an adviser who cannot find
  a transferee enters them twice, and hiding the school would trade a
  privacy gain for a duplicate-LRN loss. Delete is registrar-only —
  ON DELETE RESTRICT already blocked deleting an *enrolled* learner, so
  the button only ever bit the just-imported, not-yet-enrolled set.
  `tests/test_learner_access.py` covers it, including against the live
  sections.
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
- **Merging a *new* range over cells the template already merges needs the
  old merges undone first** — openpyxl refuses to merge a range that
  overlaps an existing one. SF9's Remarks column ships pre-merged per row
  (`L20:M20`, `L21:M21`, ...); collapsing several rows into one exit-status
  cell (§35 amendment, 2026-09-05 — `sf9_report._merge_exit_status`) calls
  `unmerge_cells()` on each row's own merge before `merge_cells()` on the
  bigger range. Confirmed empirically that `merge_cells()` clears the
  covered (non-anchor) cells' values on its own — no separate blanking
  step needed, just don't write anything to the anchor before merging or
  it'll be discarded too. This is a one-off dynamic merge outside the
  `write()`/anchor-map machinery above (which is for the template's own
  *static* merges); write the anchor's `.value` directly afterward.

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
- **A module every page imports must not reach `app.models` at load** —
  it dictates the whole app's import order, and `_helpers.py` is only the
  instance that broke. Import inside the function instead.
  `tests/test_import_order.py` enforces it; see the Python version
  section above for the outage and the one exception.
- **`st.cache_data` is shared across every signed-in user**, so its
  argument list has to carry whatever scopes the viewer. Only Insights
  uses it so far; the rules are in the Analytics section below, and one
  of its caches holds learner names.
- **A keyed widget raises when its stored value is not among its
  options**, which is what `_helpers._forget_stale()` exists for. Any
  new filter whose options depend on another filter — or on the school
  year — needs it called *before* the widget is built, not just the two
  boxes `section_filters` already draws.

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
- **Gradebook Save/Submit and Grade Summary's "Recompute all" called
  `recompute_enrollment_grades` once per touched learner** (found
  2026-09-04, reported as "saving grades takes 60+ seconds"). That
  function alone is ~25-40 queries and its own `session.commit()` — 30
  touched learners was ~30×30 ≈ 900+ round trips. `recompute_enrollment_grades_batch()`
  loads one `_RecomputeContext` for the whole batch (same split as
  `load_report_context`/`load_sf9_context`) and commits once; the maths
  in `_recompute_one` is untouched, only the data loading moved.
  `recompute_enrollment_grades(session, enrollment_id)` still exists as a
  1-element wrapper for single-learner callers (Grade Summary's per-learner
  Recompute button) — call the batch function directly for anything that
  loops. Same fix in `app/import_specs.py`'s term-grade import.
- **`attendance_service.roster_for_month` called `active_window_for` (a
  `LearnerMovement` query) and `session.get(Learner, ...)` once per
  enrollment** — 2×N round trips, and it's called several times per
  attendance page action (seed, grid, save, validate), which is what made
  preparing/refreshing a month's sheet slow. Batched the same way
  `analytics_service.attendance_risk()` already did (see the Insights
  section above) — movements via one `enrollment_id.in_(...)` query,
  windows built in Python with `compute_active_window`. No signature or
  behavior change, so SF2, `export_service`, `seed_month_records` and
  `validate_month` all benefit for free.
- **`summarize_month` was the same shape, one query per learner, called
  in a loop from six places** (found 2026-09-04 auditing "save
  attendance"/"finalize month" after the fix above): the Attendance
  page's monthly summary table and its finalization panel — which runs
  `validate_month` on **every render** once a month isn't NOT_STARTED,
  not behind a button — `export_service.attendance_export`, SF2's
  on-screen preview, and `sf2_report.py`'s printed form **twice**
  (`_learner_rows`'s per-learner summary, and `_movement_counts` re-
  querying `LearnerMovement` per learner even though `_learner_rows` two
  lines above it had just batched the identical data for a different
  purpose). `summarize_month_batch()` batches `records_for_month` once
  for the whole roster; `movements_by_enrollment()` does the same for
  `LearnerMovement`. `summarize_month` stays as the 1-enrollment wrapper.
  Verified against a throwaway 40-learner section in the live database:
  `validate_month` dropped from ~160 round trips to ~5 (0.53s), SF2
  generation similarly (2.2s total).
- **Analytics has a second cost axis: row volume.** Everything above is
  about round trips. `app/analytics_service.py` also has to not move
  ~32,000 `term_grades` into Python, so it aggregates in Postgres and
  returns tens of rows. See the Analytics section below.

## Analytics — Overview → Insights

Added 2026-08-29. `app/analytics_service.py` (queries, no Streamlit) and
`app/admin_pages/insights.py` (the page), covering eight metrics behind
one shared filter set: **grade encoding progress**, **grade
distribution**, **subject difficulty**, **learners at risk** (per term),
**annual standing**, **attendance risk (§31)**, **award eligibility
(§24)**, and — for subject teachers only — **learners at risk in their
own subjects**.

It sits beside the Dashboard rather than inside it. The split is by
question: the Dashboard answers *what is outstanding right now* and is
deliberately filter-free, Insights answers *how are we doing* and is
nothing but filters. Merging them would make a strand change re-pay for
the attendance-month table nobody moved. Spec §42 asks for role-specific
dashboards and only the administrator one existed, so this fills a gap
rather than adding scope. Admins, registrars and school heads see the
whole school; advisers see the sections they advise; subject teachers
get a different, offering-scoped view. The access rules live in the two
sections below, and they are the part of this page most worth reading
before changing anything.

**The shape: aggregate in SQL, cache once per school year, slice in
Python.** Each metric issues a fixed 8 queries for a whole year and
returns one row per section × term (90 today), or per section × term ×
subject (~810 once grades exist). The page caches that and filters the
cached rows, so **no dropdown costs a round trip**. Pushing the
grade-level/strand/section filters into SQL would put 85ms behind every
widget for no gain. `tests/test_analytics_service.py` asserts the query
count stays flat.

**This is the app's first use of `st.cache_data`, and it has rules:**

- Open the session **inside** the cached function. A `Session` is not
  cacheable and ORM instances handed across the boundary come back
  detached — which is why the service returns frozen dataclasses of
  primitives, and why `str(uuid)` goes in rather than the UUID.
- **The argument list is the cache key, and the cache is shared across
  every signed-in user.** Advisers reach the page (added 2026-08-29), so
  every cached loader takes `section_ids` and it is **in the key, not
  just in the query**. Scoping the query alone would be worse than not
  scoping at all: it would look correct and serve whichever adviser
  asked first. `None` is the whole school; an adviser passes the tuple
  of sections they hold. **Not theoretical — `_at_risk` caches learner
  names.** Same failure as the Learner Masterlist entry above, with a
  cache in front of it.
- Keep cached objects aggregate-sized. The process has ~1GB; caching a
  roster is how you evict everything else.

**Never recompute an official figure here.** Rule 1 and §65 give one
implementation of every formula, and a second one in an analytics page
would drift from the report card with nothing looking wrong. At-risk
reads `failed_subject_count` and `lowest_term_grade` straight out of
`term_grade_summaries`, where `grading_service` wrote them against the
policy in force at the time — re-deriving "who is failing" from raw
grades and today's threshold would restate a finalized term under a mark
it was never graded on, which is rule 6.

**The five judgement calls, and why they went that way:**

1. **A percentage is never averaged or summed.** `roll_up()` and
   `subject_difficulty()` recompute from the totals, because a 5-learner
   SNED section and a 45-learner one do not each contribute half. Same
   mistake that shipped twice already, in SF2 and again SF4.
2. **`percent` is `None`, never `0.0`, when nothing is expected.** A
   section with no offerings yet is not 0% encoded, it is not yet
   askable — and 0% sorts it above sections where teachers are genuinely
   late. Same reasoning as rule 2.
3. **PLACEHOLDER offerings count toward `expected`.** §48 says a
   placeholder is not a usable offering, but the Gradebook does not
   filter on offering status, so a teacher can and does encode against
   one. Excluding them from the denominator while their grades land in
   the numerator is how a section reports 104%. They are surfaced as a
   separate warning instead.
4. **Grade bands are numeric and unnamed, and anchored to the resolved
   passing mark.** The Outstanding / Very Satisfactory descriptors are
   DO 8 s. 2015, and DO 017 defers the whole assessment, grading and
   awards policy to a forthcoming order — the same reason not to move
   `passing_grade` or the award tiers. `grade_bands(passing)` builds
   them from `grading_service.resolve_passing_grade()` (added as a
   public name over the existing private resolver, so analytics reads the
   same policy row the report card does).
5. **Difficulty ranks by share below passing, not by the mean.** A
   subject can sit at a comfortable average and still have six learners
   failing, which is precisely the case a department head needs. A test
   asserts the top-ranked subject has the *higher* average so this cannot
   be "fixed" back.

**Two counting traps specific to this page:**

- **Means are only ever taken within one subject**, where every learner
  carries the same units so unit weighting has nothing to change. A mean
  *across* subjects is the unit-weighted General Average and belongs on
  the report card; the page says so rather than silently omitting it.
- **Learners and flags are different numbers.** A learner failing all
  three terms is three rows and one person. `at_risk_headline()` returns
  both; adding the rows up overstates the problem by exactly the amount
  the school most wants right.

**The at-risk list is deliberately not ordered through
`app/roster_order.py`**, which governs every other list of learners in
the app. That module puts males first because the DepEd forms do; this
is a work list, not a roster, and ordering it by anything but severity
buries the learner most at risk in the middle. The reason is written at
the sort so it does not read as an oversight. It also shows the least
that still identifies someone — name, section, term, no LRN and no
birthdate.

**Testing: `term_grades` is empty, so the SQL had never run against a
row.** Both the band-bucketing and every at-risk branch are covered by
tests that **write rows, flush, assert, and roll back** — the fixture
never commits, so nothing reaches the school's data. That is the
technique to reuse for any future metric, because the alternative is
shipping a `GROUP BY` that has never seen a grade.

**`recompute_enrollment_grades` calls `session.commit()`**
(`app/grading_service.py`), so it cannot appear in a rolled-back test —
running it against the live database writes permanently. The at-risk
test therefore builds `TermGradeSummary` rows directly. Worth knowing
before someone tries to drive the grading pipeline end to end from a
test.

**Advisers see the same page, scoped** (2026-08-29). §42 asks for an
adviser dashboard, and all four metrics turn out to be adviser questions
once narrowed — "how is my class spread", "which subject is mine
struggling in", "who is failing". So the page is one page with a scoped
data source rather than a second page:

- `SCHOOL_WIDE_ROLES` (SUPER_ADMIN, REGISTRAR, SCHOOL_HEAD) get
  `section_ids=None`; anyone else gets `advised_section_ids()`. Read off
  `role_codes`, **not `has_role`**, which treats SUPER_ADMIN as
  satisfying every check — right for page access, wrong for a data
  question.
- **`None` is the whole school; `()` is a viewer entitled to nothing.**
  Both are falsy, and treating them alike is how an adviser holding no
  section sees all ~1,200 learners. `_sections_in_scope` is the one
  place that distinction lives, and `tests/test_analytics_service.py`
  asserts the empty scope returns nothing on every metric.
- Scoping is in **SQL**; the grade-level/strand/term dropdowns still
  filter cached rows in Python. Access narrowing and display narrowing
  are different jobs and the split is deliberate — a viewer must never
  have rows loaded they are not entitled to, even to discard them.
- `advised_section_ids` compares in SQL because `AuthUser.id` is a `str`
  and `sections.adviser_user_id` is a UUID. Hold a Section object
  instead and you want `section_access.is_advised_by`.
- **An adviser may hold more than one section** — one here holds two —
  so nothing assumes a single one.

**`offering_progress()` is the adviser's actual question**, at section ×
subject × term with the assigned teacher's name from the active
`teacher_assignments` row. Section-and-term progress says whether you
are behind; this says on what and whose door to knock on. The adviser
does not encode these grades — the subject teacher does — but the
adviser holds the report card, so chasing is their job. It renders when
the view is down to **one section**, which is a state, not a role: an
adviser lands there without touching a filter, and an admin who picks a
section gets the same thing.

**A subject teacher gets a different page, not a narrower one**
(2026-08-29). This is the one scoping decision here that is a privacy
boundary rather than a convenience, so it is worth stating plainly:

- **They are scoped by offering, not by section.** An adviser owns whole
  sections; a subject teacher owns one subject inside sections whose
  other subjects belong to colleagues. The busiest teacher here holds
  **30 classes across 10 sections** — handing them those ten sections'
  ids would show them ten sections' worth of other people's grades.
- So the school-wide layout cannot simply be filtered for them.
  `_render_teacher_view` is a separate branch, and three things are
  deliberately absent: the section-level encoding table, the school-wide
  difficulty ranking, and **`at_risk_learners`, which reads whole-term
  averages across every subject** and would expose a learner's standing
  in subjects they do not teach.
- `taught_offering_ids()` is the scope, from **active** assignments —
  the same rule the Gradebook uses, so a reassigned teacher loses the
  class as it moves.
- **`subject_learners_at_risk()` is why the at-risk list could not just
  be scoped.** `at_risk_learners` reads `term_grade_summaries`, whose
  `term_average` and `failed_subject_count` describe a learner across
  *every* subject. A teacher's version is built from `term_grades` on
  their own offerings instead — "who is failing my subject" and "who is
  failing the term" are different questions with different answers, and
  only the first is theirs. A test asserts the function never mentions
  `TermGradeSummary`, because the tempting future edit is to reuse the
  existing one. The passing mark is resolved **per offering**, since
  `section_subject_offerings.grading_policy_version_id` can override it
  and `grading_service` honours that; none do today. `subject_grade_stats` and `offering_progress` both
  take `offering_ids` alongside `section_ids`; **`offering_progress`
  refuses to run with neither** rather than falling back school-wide.
- A teacher who also advises is shown the **adviser** view, the broader
  of the two entitlements. `tests/test_analytics_service.py` asserts the
  offering scope is strictly smaller than the section scope wherever a
  section runs more than one subject — the leak itself, tested.

`offering_progress` also carries `submitted`, counting
`SUBMITTED_OR_BEYOND` rather than only SUBMITTED: rule 7 runs
DRAFT → SUBMITTED → VERIFIED → FINALIZED, and a teacher told "0
submitted" on a finalized class would be chased for work already done.
Encoding and submitting are separate steps, so "encoded but not
submitted" is a real state and the page says so.

**The teacher view does not name the learners still missing a grade.**
The count is there; the Gradebook is where you act, already shows the
class with the blanks visible, and a second roster here would be one
more thing to keep in step.

**Annual standing (`annual_risk`) is the year-end counterpart of
`at_risk_learners`** (2026-08-29), reading `annual_grade_summaries`.
Three things make it more than the same query against another table:

- **It never says "will not be promoted".** DO 017 leaves retention,
  promotion, graduation and honors to a forthcoming order (§25, §26),
  and adds a rule the finalize guard does not implement — a learner
  taking more electives than the minimum must pass all of them. So it
  reports what the summary says and stops. Naming a consequence would
  be inventing school policy on a page.
- **The General Average is read, never recomputed** — unit-weighted
  under DO 017 and built from each subject's real term pattern. The
  stored `averaging_method` and `total_units` come along so the number
  can be explained rather than just displayed.
- **The failed-area list obeys §16, and this is the trap.**
  `subject_final_grades` carries a row for *every* subject including
  both Grade 11 language components, and neither component is what
  counts — the combined area's result is, once. A list built from the
  raw FAILED rows reports a learner as failing two languages when the
  pair passed, or misses a failed pair whose components each scraped
  through. `_failed_areas` applies the same substitution the General
  Average does. Both directions are tested against constructed records:
  components failing while the pair passes, and components passing while
  the pair fails.

**Only failing learners are named; incomplete records are a per-section
count.** An incomplete annual record is the other thing that blocks a
year closing, but that is currently almost every learner, so listing
them would be hundreds of names. `complete_rate` is the
finalize-readiness figure; the flagged list is the academic one.

**Attendance risk (§31) is the one metric that cannot aggregate in SQL**
(2026-08-29). Consecutive-run detection needs each learner's days *in
order*, so `attendance_risk()` is shaped differently from everything
else here:

- **Bounded to one month**, and the month is part of the query rather
  than a filter over cached rows. A month is ~20 class days × the
  roster; the record query selects three columns rather than ORM
  instances, because hydrating that many is the expensive part.
- **The §31 rule is not reimplemented.** `app/attendance_engine.py`
  already owns what an eligible class day is, that LATE and CUTTING
  count as present, that an unencoded day breaks a run, and that a run
  is counted in *class* days so a weekend does not break it. This
  batches the I/O — one query for movements, one for records — and calls
  `summarize_attendance` per learner, exactly as the Attendance page
  does. `active_window_for` is deliberately **not** used: it costs two
  round trips per learner, so windows are built from batched movements
  through the engine's own pure `compute_active_window`.
- **Only §31's warning is flagged.** No absence-rate threshold, because
  §31 names the consecutive run and nothing else — a percentage cutoff
  invented in an analytics page would read as school policy.
- Two outputs: a **short** flagged list that names people, and a section
  table in totals, so attendance can be reported without naming
  everyone.

**The trap it walked into: `absent / eligible` reads 0% on a month
nobody has encoded**, which looks like perfect attendance rather than an
empty sheet. The rate is denominated on days somebody has **actually
marked**, and is None until someone has — rule 2 wearing a different
hat. It is displayed next to `encoded_rate`, which says how much of the
month the figure rests on; 20% absence across 24% of the month is a very
different claim from 20% across all of it. Caught by seeing the real
output, not by review.

Attendance is reported by **month**, not by the page's Term filter,
because SF2 is monthly and `attendance_month_status` finalizes monthly.
Slicing it by term here would put a number on screen that no official
form could be reconciled against.

**Award eligibility (§24) reads `learner_awards`, and its whole
difficulty is the denominator** (2026-08-30). `award_eligibility()`
counts the rows `award_service.compute_award_eligibility` already wrote;
`award_policy_options()` feeds the page's own policy picker. Five things
are worth knowing before changing it:

- **"Not computed" is not "not eligible", and today it is everybody** —
  all 566 annual slots and all 1,698 term ones are unjudged, because a
  `learner_awards` row exists only after someone presses *Compute
  eligibility for all* on the Awards page for that section. So the
  eligible share is denominated on the learners **judged**, never on the
  roster, and is `None` until one is. It sits next to the share of the
  roster judged, the same pairing as attendance's `absence_rate` beside
  `encoded_rate`, and for the same reason: "3 of 4 judged" and "3 of 40"
  are different claims.
- **Nothing is re-judged.** §24's rules — complete record, derogatory
  record, minimum average, the tier ladder, the required reason — live
  in `app/award_service.py`. A second evaluator here would eventually
  name a learner the Awards page will not certify. Tier counts come from
  the stored `award_name`, never re-derived from the average against
  `tier_thresholds`.
- **A stale award is worse than a missing one, because it looks
  answered.** `learner_awards.computed_at` against the summary's own
  `computed_at` catches a result judged before the average it was judged
  on last moved. Flagged and named, never refreshed — recomputing writes,
  and this page writes nothing. `_is_stale` normalises both sides: they
  are written tz-aware into `TIMESTAMP WITHOUT TIME ZONE` columns, so a
  value read from Postgres is naive while one still sitting in the
  session from an uncommitted write is not.
- **An override is counted on its own axis and never folded in** (§40,
  §67), and is never stale — `compute_award_eligibility` deliberately
  leaves those rows alone, so it is not waiting for a recompute.
- **One policy version at a time**, chosen by a picker of the section's
  own, like the attendance month. Academic Excellence is annual on the
  General Average; Legacy Tiered Honors is per term on the Term Average.
  A learner can hold both, so a combined count means nothing. A TERM
  policy also produces one row per section **per term**, so with more
  than one term in view every count is of learner-terms, not learners —
  the labels say so, and the eligible list reports both numbers. Same
  trap as learners-versus-flags on the at-risk list.

`tests/test_insights_awards_render.py` is the first test here that
renders a piece of the page through Streamlit's own runtime rather than
testing the service under it. The award section has the most conditional
layout on the page — a Term column only for a per-term policy, three
warnings that each appear only when true, a headline whose labels change
with the terms in view — and none of it can be reached without an
account holding the right role. `AppTest` still has no browser (see
`tests/test_add_form_reset.py`), so it proves the script runs and the
tables are built, not what they look like.

**Still open on this page:** nobody has viewed it signed-in — every
function is verified against real and constructed data, and the award
section's layout is now driven through `AppTest`, but the page as a
whole is unseen, and the adviser and teacher paths especially so, since
neither can be reached without an account holding that role. Annual
standing, attendance risk and award eligibility are all absent from the
subject teacher view: each describes a learner across every subject,
which is not a subject teacher's to see, and neither the Attendance nor
the Awards page admits them either.

## Where things stand

**The app is live** at https://fgnmhs-shs.streamlit.app, deployed
2026-08-12 to Streamlit Community Cloud from GitHub (branch `master`,
entrypoint `streamlit_app.py`, secrets in the app's Settings panel).

**If a change reaches the running app, treat it as a live change** —
schema, anything on an import path, or the shape of what's held in
session state. Migrations follow the ordering in `docs/operations.md`,
and a restart signs everyone out and loses unsaved grade entry, so deploy
those outside encoding hours.

Docs, tests, and scripts nothing imports carry none of that — push them
freely. They can't reach a running process anyway; only a reboot loads
new code, and the point of the rule is the reboot, not the push.

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
**Overview → Insights** exists as of 2026-08-29 — encoding progress,
grade distribution, subject difficulty and learners at risk, filtered
and cached; see the Analytics section above. Encoding progress is the
one to watch before the deadline: it reads 0 of 7,260 expected today,
and shows the Grade 12 term gap as a number (708 expected against Grade
11's 6,552).
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
      **Known ahead of the file itself** (2026-09-02, from the user, not
      yet verified against the actual template): **Folio (8.5 × 13in),
      portrait** — unlike SF2/SF9, which are both landscape. And the
      form is **two pages, one grade level per side**: Grade 11 on the
      front, Grade 12 on the back. That second part means the single-
      worksheet-per-form shape SF2/SF9 use won't carry over as-is —
      `app/excel_template.py`'s renderer reads geometry off whatever
      worksheet it's given, so two worksheets (or two print areas) is
      no new layout code, but `sf9_report.load_sf9_context()`'s
      per-grade-level query shape is the closer model than SF9's own
      single-grade-level sheet. Confirm both details against the file
      once it arrives — this is the user's recollection, not something
      read off a template.
- [ ] Also still open: §37's Grade 11 prior-entry/eligibility fields
      (PEPT, ALS A&E, CLC, previous school) already exist in
      `learner_admission_records` and are deliberately NOT snapshotted —
      they describe a single admission event rather than a year's result.
      Revisit if the SF10 layout needs them frozen too.
