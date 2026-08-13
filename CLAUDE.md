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
   Komunikasyon): each is graded separately per term (both count separately
   in the Term Average), but for the annual General Average the two are
   combined into ONE virtual learning area (average of their two finals).
   On Grade 11 SF9, the two component subjects show their term grades but
   their individual Final Grade cells stay BLANK — only the combined parent
   row shows a Final Grade. Modeled via `combined_learning_areas` /
   `combined_learning_area_components` tables, not hardcoded logic — this
   rule is unit-tested (see spec Section 68, Test A) and is a major source
   of General Average bugs if implemented wrong.
4. **General Average is NOT the average of the three Term Averages.**
   It's computed from applicable subject Final Grades (which respect each
   subject's actual term-offering pattern — some electives run one term,
   some two, some all three).
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
  the browser — use `_helpers.flash()` / `render_flashes()`.
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

**Treat every change as a live change.** `git push` redeploys; a restart
signs everyone out and loses unsaved grade entry; migrations must follow
the ordering in `docs/operations.md`. Deploy outside encoding hours.

Full phase-by-phase build history, including the reasoning behind every
decision above: see `docs/build-log.md` (not auto-loaded — read it when you
need the history).

**Remaining before real use:** migrating the rest of the ~1,200 learners
(the database currently holds 22 learners across 27 sections), teacher
accounts and assignments, filling in the empty subject profiles, and an
end-to-end dress rehearsal on one section.
**Term 1 closes 15 September 2026.**

### Still open

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
