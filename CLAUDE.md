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

## Current status

- [x] Stack decided (see above)
- [x] Phase 1 DB schema/ERD drafted, field-level — see `docs/schema.md`
      (10 domains, 42 tables: org, users/RBAC, academic structure,
      subjects/grading policy, learners/enrollment, grades, attendance,
      awards, reports, admin)
- [x] Resolved: `programs` dropped as a separate table — `tracks` →
      `strands` covers both the Academic ("Program/Cluster": ASSH/BE/STEM)
      and TechPro ("Strand/Specialization": ICT/HE/CP/EMS) usages the spec
      uses interchangeably. See `docs/schema.md` Academic Structure section.
- [x] The long-deferred `report_snapshots` idea landed in Phase 11 as the
      **permanent learner academic record** (`app/models/academic_record.py`)
      — see the Phase 11 entry below. It freezes the learner's *result*
      rather than a rendered file, which is what makes a later template
      revision reprintable without recalculating grades (§36.4).
- [x] SQLAlchemy models written (`app/models/`, one file per domain) and
      first Alembic migration (`alembic/versions/`) generated + applied to
      the live Supabase DB — all 42 tables confirmed present, `alembic
      check` reports no drift. Connects via the Session Pooler
      (`aws-0-ap-northeast-1.pooler.supabase.com:5432`, user
      `postgres.<project-ref>`), not the direct `db.<ref>.supabase.co`
      host — that host is IPv6-only and unreachable from networks without
      IPv6 routing. `.env` holds `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`/
      `DB_PASSWORD` as separate values (see `.env.example`), assembled via
      `sqlalchemy.URL.create()` in `app/database.py` — never hand-build a
      single `DATABASE_URL` string, since an unescaped special character
      in the password will misparse and can leak the password into error
      text.
- [x] Phase 2 reference/catalog data seeded (`app/seed.py`, idempotent —
      safe to re-run, never overwrites existing rows): school identity
      (DepEd School ID 301040, Region III, Schools Division of Angeles
      City), 6 roles, 2 grade levels, 2 tracks, 7 strands, 7 subject
      categories, SY 2026-2027 with real Term 1/2/3 dates, grading policy
      (passing grade 75), 32 subjects, and the G11 combined-language
      area wired to Effective Communication + Mabisang Komunikasyon.
      `CORE_SUBJECT` vs `OTHER_ACADEMIC_ELECTIVE` were split from the
      spec's single combined category after user feedback — see
      [[project-core-vs-elective-subjects]] memory / `docs/schema.md`
      `subject_categories` note for why this matters for Final Grade
      computation later.
      Not seeded (built as admin CRUD instead, Phase 3): sections.
      Not built yet: subject profiles, section subject offerings
      (Phase 4).
- [x] Phase 2 Streamlit admin CRUD built and verified end-to-end against
      the live app (login → edit → re-render, delete-blocked-by-FK, add
      user): `streamlit_app.py` (entrypoint — **must stay at repo root**,
      not inside `app/`, or `from app.* import` fails since Streamlit adds
      only the entrypoint's own directory to `sys.path`, not the repo
      root), `app/auth.py` (login/session/RBAC via `st.session_state`,
      `require_role()`), `app/user_provisioning.py` (shared by the in-app
      "Add User" screen and `scripts/bootstrap_admin.py`, the one-time
      first-admin script — run via `python -m scripts.bootstrap_admin`,
      not as a bare script, same repo-root import reason), 6 pages under
      `app/admin_pages/` (School Info, School Years & Terms, Academic
      Structure, Subject Catalog, Grading Policy, Users & Roles).
      Two Streamlit gotchas hit and fixed, worth knowing before adding
      more pages: (1) `st.success()`/`st.error()` called immediately
      before `st.rerun()` never reach the browser — the rerun restarts
      script execution first. Use `app/admin_pages/_helpers.py`'s
      `flash()`/`render_flashes()` instead of calling `st.success`/
      `st.error` directly whenever a `st.rerun()` follows in the same
      branch. (2) `st.tabs()` always resets to the first tab after a
      rerun — use `_helpers.py`'s `stateful_tabs()` instead for any new
      tabbed page. `_helpers.py` also has `try_delete()` (catches the
      `IntegrityError` from an `ON DELETE RESTRICT` FK and flashes a
      friendly message instead of crashing).
      Known limitation, not solved: `st.session_state` resets on a hard
      page refresh or new tab, so logging in again is needed after
      either — fine for a handful of admin users, revisit with a
      cookie-based session plugin if it becomes annoying.
- [x] Phase 3 (Learners and enrollment) built and verified end-to-end:
      **Sections** page (Super Admin only, per spec §3A — filters adviser
      choices to users holding the `ADVISER` role), **Learner Masterlist**
      page (`learners` + 1:1 `learner_admission_records`, Super Admin +
      `REGISTRAR`), **Enrollment** page (two tabs: enroll a learner into a
      section for a school year; section roster with per-enrollment status/
      derogatory/comments editing and `learner_movements` logging — logging
      a movement also updates `enrollments.enrollment_status`, the
      denormalized "current status" per `docs/schema.md`). `REGISTRAR` is
      now the first non-Super-Admin role wired into `streamlit_app.py`'s
      navigation — it only sees Learner Masterlist + Enrollment, not the
      Super-Admin-only config pages.
      New shared helper: `app/admin_pages/_helpers.py`'s `try_commit()`
      (catches `IntegrityError` on save, not just delete — needed once
      real constraints like the 12-digit LRN format/uniqueness were in
      play, not just FK `RESTRICT`s).
- [x] Phase 4 (Subject profiles and section-specific subject offerings)
      built and verified end-to-end, both Super-Admin-only: **Subject
      Profiles** page manages `subject_profiles` + `subject_profile_subjects`
      (per-track/strand default subject sets with term1/2/3-active flags —
      seed data only, §7/§12-13). **Section Offerings** page manages
      `section_subject_offerings` — the actual single source of truth for
      what a learner is graded on (§48) — with a "seed from profile" action
      that bulk-creates offerings from a matching profile (skips
      already-offered subject/term pairs rather than erroring) plus manual
      add/edit/delete. Category and required-flag are per-offering
      snapshots/overrides, not just inherited from the subject, matching
      the schema's snapshot-for-history design.
      `teacher_assignments` (referenced by `section_subject_offering_id`)
      is intentionally not built yet — that's Phase 5.
- [x] Phase 5 (Teacher assignments and term gradebooks) built and verified
      end-to-end. **Teacher Assignments** page (Super Admin): assigns a
      `SUBJECT_TEACHER`-role user to a `section_subject_offering`;
      reassigning deactivates the old `teacher_assignments` row
      (`is_active=False`, `unassigned_at` stamped) rather than deleting it
      (§47 — auditable). School Years & Terms now also exposes each
      term's `grade_encoding_status` (OPEN/CLOSED) — grading is blocked
      everywhere until a Super Admin opens it. **Gradebook** page (new
      role wired into `streamlit_app.py`: `SUBJECT_TEACHER`) — a teacher
      picks one of their active assignments, sees the section roster
      (active enrollment statuses only, same spirit as SF2's "no longer
      active" rule), edits `official_grade` per learner, Save (upserts,
      change-detected — untouched rows aren't touched), and Submit
      (bulk `DRAFT`→`SUBMITTED` for the offering/term). A grade stays
      editable through `SUBMITTED` — editing one reverts it to `DRAFT` so
      it's clear it needs re-submission; only `VERIFIED`/`FINALIZED` lock
      (no reopen workflow built yet — Phase 7). `SUPER_ADMIN` now
      implicitly satisfies every `require_role()` check
      (`app/auth.py` `AuthUser.has_role()`) — reachable pages, not shown
      data, are what's implied; a Super Admin with no real
      `teacher_assignments` row still sees an empty Gradebook.
      Two pre-existing bugs (present since Phase 2, only just triggered)
      fixed along the way: `school_years.py` was incrementing `.version`
      on `Term` and `SchoolYear`, neither of which has `VersionMixin`/a
      `version` column — don't add `VersionMixin` speculatively to a
      model without checking `docs/schema.md` first, and don't copy the
      `.version += 1` pattern onto a new model without confirming it
      actually has that column.
- [x] Phase 6 (Grade computation engine, incl. combined-language rule)
      built and verified end-to-end. **`app/grading_engine.py`** — pure,
      DB-free functions (`compute_subject_final_grade` respects each
      subject's actual required-term set from `section_subject_offerings`,
      never assumes 3 terms; `compute_combined_language_term_grade`/
      `_final_grade`; `compute_general_average`; `determine_pass_fail`).
      Rounding is explicit round-half-up (`Decimal` + `ROUND_HALF_UP`), not
      Python's built-in `round()` (round-half-to-even) — the spec's own
      worked example rounds 92.5 → 93, which `round(92.5)` gets wrong (→92).
      **`tests/test_grading_engine.py`** — pytest, implements master-spec.md
      §68 Tests A/B/C/F (D and E belong to SF2 and Awards, later phases);
      `pytest` added to `requirements.txt`; run via
      `./.venv/Scripts/python.exe -m pytest tests/`. **`app/grading_service.py`**
      — `recompute_enrollment_grades(session, enrollment_id)` reads
      `term_grades`, writes `subject_final_grades`/
      `combined_learning_area_results`/`annual_grade_summaries`; resolves
      passing grade from the offering's `grading_policy_version_id` or
      falls back to the ACTIVE version for the school year. Called
      automatically after Gradebook Save/Submit, plus a manual "Recompute"
      button. **Grade Summary** page (Super Admin/Registrar/Adviser,
      Adviser scoped to their own section) — class-wide gradesheet with a
      Term 1/2/3/Final view selector (one column per subject, combined-
      language pair shown as plain subjects here) and a per-learner detail
      view that *does* follow the SF9 display rule (§16): combined parent
      row + indented component rows with blank Final Grade cells. Grades
      display as whole numbers everywhere (DB is `Numeric(5,2)`, always
      integral by construction — `int()` for display, not stored
      differently); Gradebook rounds a teacher's typed entry with the same
      `round_half_up` before saving.
      Also this phase: CSV bulk-add on Learner Masterlist and Subject
      Catalog (`app/admin_pages/_helpers.py`'s `read_uploaded_csv` +
      per-page row validation — lightweight, not the full formal §51
      import pipeline, that's still Phase 13); bulk enrollment on the
      Enrollment page (multiselect); `ADVISER` wired into Learner
      Masterlist/Enrollment/Grade Summary, scoped to
      `sections.adviser_user_id == current_user.id` (DepEd advisers pick
      up registrar-adjacent duties for their own section, §3C); a new
      partial unique index enforces one adviser per section per school
      year (`sections`, migration `14e55ba4624b`).
      **Two important bug classes found and fixed, worth knowing before
      writing more pages:** (1) Several forms called raw `session.commit()`
      instead of `_helpers.py`'s `try_commit()` — harmless until a real
      constraint violation hit it (e.g. a duplicate LRN), which then
      crashed the whole page with an unhandled `IntegrityError` instead of
      showing a friendly message. Audited and fixed 8+ call sites across
      Sections/Academic Structure/School Info/School Years/Subject
      Catalog/Grading Policy — always use `try_commit`/`try_delete` for
      any commit that could plausibly violate a unique/FK constraint, never
      a bare `session.commit()`. (2) This codebase declares **no ORM
      `relationship()`** between models anywhere (explicit queries only,
      by design) — which means SQLAlchemy's unit-of-work can't infer that
      a new parent row (e.g. `Learner`) must be inserted before a new
      dependent row (`Enrollment`) that references it via a bare FK
      column, and can attempt them in the wrong order, failing a foreign
      key check. Hit in both "add learner + optionally enroll" and
      "create school year + terms". Fixed with a new
      `_helpers.py` `flush_or_rollback(session)` — flush the parent
      explicitly (inside the same friendly-error handling as
      `try_commit`) *before* constructing the dependent row, so its
      generated id is available and insert order is guaranteed. Any future
      page that creates two related new rows in one action needs this
      pattern — don't assume SQLAlchemy will order it correctly on its own.
- [x] Phase 7 (Annual summary, validation, finalization, awards) built.
      Award policy shapes both live in `award_policy_versions`: single-tier
      (flat `min_general_average`/`min_lowest_final_grade`, e.g. Academic
      Excellence DO 15 s.2026) and tiered (`tier_thresholds` JSONB list,
      e.g. Legacy Honors WITH HIGHEST/HIGH/HONORS — picks the highest GA
      tier cleared). Both seeded in `app/seed.py`, both require an
      explanatory reason even when NOT_ELIGIBLE (§24 — never a bare
      boolean). **`app/award_service.py`** — `compute_award_eligibility()`
      reads the already-computed `annual_grade_summaries` row (never
      recomputes grades itself), `set_award_override()`/
      `clear_award_override()` for the audited manual-override path
      (§40/§67 — override always requires a reason; an overridden row is
      skipped on the next recompute until explicitly cleared).
      **`app/certificate_generator.py`** — `generate_award_certificate()`,
      direct ReportLab generation (landscape Letter) matching the user's
      supplied mockup exactly: seal (`app/assets/fgnmhs_seal.png`,
      extracted from the SF9 template's embedded `image2.png` — the actual
      FGNMHS star seal, not the generic DepEd shield which is `image1.png`),
      header block, "CERTIFICATE OF RECOGNITION" title, learner name,
      dynamic award sentence with GA, "Given this {ordinal} of {Month
      Year} at {venue}..." sentence, two signature blocks (adviser +
      school head). Chosen over the openpyxl+LibreOffice pipeline used for
      SF9/SF2 because this is a new custom design with no pre-existing
      official template to match pixel-for-pixel. A test PDF was generated
      and visually compared against the mockup (rendered via a
      temporarily-installed `pymupdf`, not added to `requirements.txt`),
      then reviewed and approved by the user. Layout note: the
      title-through-date block is positioned off the page's *vertical
      centre* via `_BODY_BLOCK_HEIGHT`, not off the bottom of the header —
      anchoring it to the header left a dead gap above the signatures.
      That constant is the sum of the y-steps between those five lines;
      keep it in sync if any of those steps change.
      **`app/admin_pages/award_policy.py`** (Super Admin only) — manage
      policies/versions, fixed 3-tier editor (`_tier_editor`, not a
      general N-tier builder — Streamlit forms can't grow/shrink widget
      counts dynamically). **`app/admin_pages/awards.py`**
      (SUPER_ADMIN/REGISTRAR/ADVISER, adviser-scoped like Grade Summary) —
      pick school year/section/policy version, "Compute eligibility for
      all", per-learner expander with result/reason/override form,
      certificate download (guarded — shows an info message instead of
      crashing if `school_years.recognition_date` is unset, which it
      currently is for the seeded SY; set it on School Years & Terms
      before generating a real certificate).
      **Finalize/Reopen** added to Grade Summary's per-learner detail
      (`_finalization_section()` in `app/admin_pages/grade_summary.py`) —
      straightforward-button scope per user's explicit choice, not a
      workflow with intermediate approval steps. Finalize is disabled
      until `annual_grade_summaries.completion_status == COMPLETE` (§23);
      on finalize, creates a `grade_finalization_records` row
      (`scope_type=ANNUAL_ENROLLMENT`) and cascades every one of the
      enrollment's `term_grades` to `FINALIZED` (locking them — matches
      the existing Gradebook rule that only VERIFIED/FINALIZED lock).
      Reopen is Super-Admin-only, requires a reason, sets the record to
      `REOPENED` and reverts any `FINALIZED` term grades back to `DRAFT`
      for re-submission (mirrors the "editing a SUBMITTED grade reverts it
      to DRAFT" pattern from Phase 6).
      Award Policy and Awards wired into `streamlit_app.py` navigation.
      `pytest tests/` still 7/7 passing; app boots and the login page
      renders cleanly (no import errors from the new pages) — full
      click-through (award policy config → eligibility compute →
      certificate download → finalize → reopen) not yet done by the user.
- [x] Phase 8 (Academic calendar and attendance) built; not yet tested by
      the user in the live app. Same engine/service split as Phase 6:
      **`app/attendance_engine.py`** is pure and DB-free (calendar day
      generation with per-date term assignment, learner active-window
      reduction from `learner_movements`, eligible class days, attendance
      counts, five-consecutive-absence runs), **`app/attendance_service.py`**
      only reads/writes rows. **`tests/test_attendance_engine.py`** — 18
      tests incl. master-spec §68 **Test D** (transferred out in September
      → appears on September SF2, gone from October); suite is now 25/25.
      Four rules worth knowing before touching this area:
      (1) **Eligible class days are per-learner, not per-section** — a
      late enrollee has none before their effective date, a transferee
      none after. Counting a section-wide total against everyone inflates
      absences.
      (2) **"No longer active" and "don't show them" are different
      questions** (§32), so they're two functions: `is_active_on` vs
      `appears_in_month`. A learner who exits mid-month still appears on
      *that* month's sheet with a remark and only drops off the next one.
      An exit is inclusive of its effective date.
      (3) **An un-encoded attendance day is not "present"** — the
      attendance analogue of the NULL-is-not-zero grading rule, and what
      makes §33's missing-attendance check possible at all. Note this
      inverts the *paper* form's convention (blank = present), so the
      Attendance page's "Prepare / refresh this month's sheet" button
      materialises an explicit PRESENT row per learner × class day; blank
      then keeps meaning "nobody has said yet". Re-run it after a late
      enrollee joins or the calendar changes.
      (4) **LATE and CUTTING still count as days present** — the learner
      was in school. They're separate counters, not a third presence
      state.
      **Academic Calendar** page (Super Admin) — "Generate calendar" is
      re-runnable and never touches an existing date, so an override is
      never silently reverted; only missing dates are added. Weekdays
      inside a term become class days; weekends and the national **regular**
      holidays don't. `philippine_regular_holidays()` covers everything
      derivable from the year alone: the fixed dates, National Heroes Day
      (last Monday of August), and Maundy Thursday/Good Friday via
      `easter_sunday()` (anonymous Gregorian algorithm, verified against
      `dateutil.easter` for 1900-2100 — dateutil was a throwaway check,
      not a dependency). Deliberately **not** guessed at, and the line not
      to cross: lunar holidays (Eid'l Fitr/Eid'l Adha), *special*
      non-working days (All Souls', Immaculate Conception, Chinese New
      Year, EDSA), and local suspensions — all proclamation-dependent, and
      a wrong guess silently changes every learner's eligible class-day
      count. There's a test asserting those specific dates stay absent.
      With the seeded SY 2026-2027 this lands 9 of 11 months exactly on
      §28's workbook counts (203 vs 201 total); only November and December
      need a manual mark. The page shows a per-month generated-vs-target
      diff so those two are obvious. Changing a date's class-day
      status requires a reason (§28) and renumbers `class_day_sequence`
      across the whole year (it's a running count, so one flip shifts
      every later day).
      **Attendance** page (Adviser own-section + Super Admin/Registrar) —
      editable month grid via `st.data_editor` (learners × class days,
      codes P/X/T-L/T-C per §30, `·` for days outside a learner's
      window), per-learner monthly summary, and the §33 workflow
      NOT_STARTED → OPEN → FOR_REVIEW → FINALIZED with a pre-finalization
      validation panel (missing attendance and out-of-school-year movement
      dates block; five-consecutive-absence runs warn). Reopen is
      Super-Admin-only and requires a reason.
      Two gotchas hit here, both worth reusing: (a) a `SelectboxColumn`
      cell whose value isn't in `options` renders *empty*, which made
      out-of-window days look un-encoded — the sentinel has to be a valid
      option. (b) Don't call a `get_or_create_*` helper on a page's plain
      view path: it INSERTs on every render, leaving an uncommitted insert
      open and racing concurrent users into a unique violation. Split it
      into a read-only `get_month_status` for viewing and
      `get_or_create_month_status` for paths that actually commit.
      `pandas` is now a direct dependency (the grid round-trips a
      DataFrame), not just a transitive Streamlit one.
- [x] Award scope split (post-Phase-8 change, user-directed): the two
      policies are judged against **different averages at different
      frequencies**, so `award_policy_versions.scope` (`AwardScope`,
      migration `a5defba95bef`) now carries that:
      **Legacy Tiered Honors = TERM** — evaluated once per term against
      that term's Term Average, so a learner can make Honors in Term 3
      and miss it in Terms 1-2. **Academic Excellence = ANNUAL** —
      evaluated once against the year's General Average, unchanged.
      Scope and tier-shape are **orthogonal**: either scope can use flat
      thresholds or a tier ladder, and `award_service._evaluate()` handles
      all four combinations off one code path.
      This required Term Average, which did not exist before this change:
      **`compute_term_average()`** (§17) plus a new **`term_grade_summaries`**
      table (term_average / lowest_term_grade / failed_subject_count /
      completion_status per enrollment per term), written by
      `recompute_enrollment_grades` alongside the annual summary.
      **The rule to not get wrong:** the Term Average counts the Grade 11
      language pair as **two separate subjects** — §17 says outright "Do
      not substitute the combined language grade when calculating the
      Term Average", and its worked example lists seven entries including
      both. That is the exact opposite of the General Average rule (§19),
      where the pair collapses into one combined learning area. There's a
      test showing the two produce different numbers (90 vs 93) from the
      same grades.
      `learner_awards` gained a nullable `term_id` (NULL = annual). Its
      old single UNIQUE became **two partial unique indexes** — a plain
      UNIQUE over a nullable column can't stop duplicate annual rows,
      because in SQL NULL never equals NULL.
      Two Alembic gotchas hit generating that migration, both likely to
      recur: (1) autogenerate emits a bare `sa.Enum` inside `create_table`
      for an enum type that **already exists**, re-issuing CREATE TYPE and
      failing — use `postgresql.ENUM(..., create_type=False)`. (2)
      `op.add_column` does **not** auto-create a new enum type the way
      `create_table` does — call `.create(op.get_bind(), checkfirst=True)`
      first.
      The migration also carries two data fixes: it sets the seeded
      Legacy Honors version to TERM (the new column defaults everything
      to ANNUAL), and deletes award rows that were computed annually
      against a now-TERM-scoped policy — those carry `term_id IS NULL`
      and are unreachable by the new code. Manual overrides are
      deliberately spared, since they carry a human decision.
      Certificates take an optional `term_name` so a term award cites a
      "Term 3 Average" rather than claiming a General Average the learner
      hasn't earned yet. Grade Summary's per-learner detail now shows the
      three Term Averages.
- [x] Phase 9 (SF2) built; not yet clicked through by the user.
      **`app/sf2_report.py`** fills `sf-templates/SF2-template-with-sample-
      data.xlsx` via openpyxl (exact layout/merges/fonts/logos preserved,
      per the stack decision), **`app/pdf_convert.py`** flattens it with
      headless LibreOffice, **`app/admin_pages/sf2.py`** is the page
      (Adviser own-section + Super Admin/Registrar), **`tests/
      test_sf2_report.py`** covers the pure rules; suite is now 58.
      **LibreOffice is installed as of Phase 12**, so PDF export works
      for SF2/SF9 (verified end-to-end: SF2 → 2 landscape pages, SF9 → 1).
      `find_soffice()` probes the two standard Windows install paths as
      well as PATH, since LibreOffice usually isn't on PATH there; if it
      ever goes missing the pages fall back to offering the .xlsx with an
      explanation rather than erroring.
      **The template arrives tethered to another workbook** — ~1600 of its
      data cells are external-link formulas pointing at the school's
      master automation workbook on OneDrive (`'[1]ATTENDANCE DAILY'!…`,
      `'[1]SETUP'!…`). `strip_external_formulas()` blanks every one before
      writing, `workbook._external_links = []` drops the link definition,
      and `assert_no_external_links()` raises if any survive, so generated
      files never prompt "update links?" or show stale numbers. Expect the
      same for SF9/SF10 in Phases 10-11 — those templates come from the
      same master workbook.
      Four openpyxl traps hit here, all likely to recur on SF9/SF10:
      (1) **Merge anchors differ row by row.** A day column that anchors a
      merge on the weekday row sits mid-merge on the date row, and writing
      to a non-anchor merged cell raises. `_anchor_map()` resolves every
      (row, col) to its top-left once per sheet; all writes go through
      `_write()`/`_write_ref()`. Never write via `ws.cell(...).value =`
      directly in these modules.
      (2) **`copy_worksheet` silently drops images**, so page 2 of a
      multi-page form printed without the DepEd/school seals.
      (3) **A loaded image's bytes can only be read once** — `_data()`
      consumes the BytesIO and leaves it closed, so sharing one image
      object across sheets saves the first and raises "I/O operation on
      closed file" on the second. `_replicate_images()` captures the bytes
      once and builds a fresh `Image` per sheet; there's a regression test.
      (4) **Percentages must not be summed.** The summary box's M/F/Total
      row defaults Total to M+F, which is right for counts and for average
      daily attendance but reports 200% for the percentage rows — those
      pass an explicitly recomputed total.
      Two data rules worth keeping: the printed form's **blank means
      present** (§30) — the inverse of the encoding UI's explicit "P",
      which is why the on-screen grid and the printed form deliberately
      differ; and "Enrolment as of (1st Friday of June)" is anchored to
      the **first Friday on or after the school year start**, not June's
      calendar first Friday, which for SY 2026-2027 (opens Mon 8 June)
      falls on the 5th when nobody is enrolled yet and would report zero.
      Pagination (§34) is per-sex: 25 male + 25 female rows per page, and
      the page count follows whichever sex overflows further, not the
      combined total — 30M+30F is 60 learners but only 2 pages. Verified
      end-to-end with a synthetic 60M/30F roster.
- [x] Phase 10 (SF9) built; not yet clicked through by the user.
      Two refactors came first, both worth knowing:
      **`app/excel_template.py`** now holds the template plumbing that
      SF2 and SF9 (and SF10 later) share — `strip_external_formulas`,
      `assert_no_external_links`, `anchor_map`/`write`/`write_ref`,
      `replicate_images`, `clear_column`, `workbook_to_bytes`. All four
      openpyxl traps from Phase 9 live here now, so SF10 gets them free.
      **`app/report_card.py`** is the *single* implementation of the §16
      combined-language display rule, used by **both** the Grade Summary
      screen and the generated SF9. That's deliberate: it's the rule
      CLAUDE.md flags as the biggest source of bugs, and having the
      screen and the printed card derive rows separately would invite
      them to disagree — with the printed one being what goes home to a
      parent. `build_learning_area_rows()` returns parent rows carrying a
      Final Grade and component rows with `final_grade=None` *because
      §16 says the cell is blank*, not because the value is unknown (it
      exists in `subject_final_grades`).
      **`app/sf9_report.py`** fills `sf-templates/SF9-template-with-
      sample-data.xlsx` (42 rows × 27 cols, 109 external-link formulas,
      same OneDrive master workbook as SF2). **`app/admin_pages/sf9.py`**
      is the page (Adviser own-section + Super Admin/Registrar) with an
      on-screen preview of the same rows the form prints.
      **One template serves both grade levels.** §35 asks for separate
      G11/G12 templates, but the supplied file is already grade-aware —
      its own formulas branch on `SETUP!$B$13=11` to decide whether to
      draw the combined-language hierarchy. Since we replace those
      formulas with values anyway, the distinction is purely data-driven:
      a G11 enrollment has a combined learning area, a G12 one doesn't.
      Layout: learning areas occupy rows 20-31 (12 rows, bounded by the
      "General Average" label at row 32 — there's a test asserting
      `MAX_LEARNING_AREAS` stays derived from that), A:G = name,
      H/I/J = terms, K = final, L:M = remarks. Attendance is P..Z
      (Jun-Apr) + AA total, rows 4/5/6.
      Three rules specific to this form:
      (1) **Age is taken at the school year's start**, not today — a card
      reprinted years later must still show the age the learner was.
      (2) **A month with nothing encoded is omitted entirely**, not
      printed as "22 class days, 0 present" — on a card going home to a
      parent that reads as the learner having missed the whole month.
      Totals sum only the months actually shown.
      (3) **Class days are the learner's eligible days**, not the
      section's calendar total (§31), so a late enrollee isn't shown
      absent for weeks before they arrived.
      The month header row is an external formula in the template, so it
      gets stripped with everything else and has to be written back — an
      easy one to miss, since the row looks static.
      **The biggest trap on this form, and a fifth openpyxl/Excel one for
      the list: the template blocks out non-offered terms with its own
      CONDITIONAL FORMATTING, driven by helper column N.** Three rules
      shade H/I/J when the matching digit of N is zero —
      `INT($N20/100)=0`, `MOD(INT($N20/10),10)=0`, `MOD($N20,10)=0` —
      so N is a 3-digit per-term flag (111 = all three terms, 100 = Term 1
      only, 10 = Term 2 only, 1 = Term 3 only). Writing that flag is all
      that's needed; the grey, the white text on top and the block-out
      pattern are the official template's own styling. Two ways this went
      wrong before being understood: (a) column N was mistaken for print
      scaffolding and blanked, making every digit 0 and greying out
      *every* grade on the card; (b) hand-painting fills instead — and
      "clearing" the others with `PatternFill(fill_type=None)`, which
      serialises onto OOXML fill index 1, and index 1 is **always
      gray125**, so the supposedly-cleared cells came out grey too.
      Before adding fills to any of these templates, check
      `worksheet.conditional_formatting` first — the form may already do
      it. `report_card.LearningAreaRow.offered_terms` is what feeds the
      flag, and exists precisely to tell "subject doesn't run that term"
      apart from "runs but not yet encoded"; they look identical in
      `term_grades` but mean opposite things.
      Print setup (the template ships with no `<pageSetup>` at all):
      landscape, `fitToWidth=1` **and** `fitToHeight=1` for a single
      sheet, margins narrowed 0.75"→0.25" (the card is ~9.5" tall, so the
      original margins forced a ~73% shrink; 0.25" gets it to ~84%), and
      `printOptions horizontal/verticalCentered` — without those the
      height binds first and the card parks against the left margin.
      `pytest tests/` is 137 at this point. PDF export was unavailable
      when this phase was built and started working in Phase 12 once
      LibreOffice was installed — SF9 converts to exactly one landscape
      page, confirming the fit-to-page settings survive the conversion.
- [~] Phase 11 (Temporary SF10) — **the record layer is built; the report
      layout is BLOCKED.** `sf-templates/` still has only SF2 and SF9; the
      school's SF10 file hasn't been supplied, so nothing can be filled
      yet. That blocks only the rendering: §36 orders this phase the other
      way round anyway ("create the underlying permanent learner academic
      record database independently from the report layout… never design
      the database around the visual coordinates of the temporary SF10"),
      so the record was built first and is layout-agnostic.
      **`app/models/academic_record.py`** — three tables (migration
      `f00c90460cb9`, 46 tables total): `learner_academic_records` (one
      finalized year per enrollment), `learner_academic_record_subjects`
      (one per learning area), `learner_academic_record_terms` (per-term
      averages and adviser comments).
      **The design rule, and the whole point of §38: everything
      descriptive is stored as TEXT, not as a foreign key.** Subject name,
      code and category; school name and DepEd ID; section, track, strand;
      the grading policy's passing grade as a *number*. §38 is explicit —
      "if administrators rename a subject or change a policy in a later
      school year, historical SF10 records must NOT change" — and
      pointing at `subjects.id` would make a rename in SY 2028-2029
      silently rewrite a learner's SY 2026-2027 record. The FK columns
      that remain (`enrollment_id`, `learner_id`, `subject_id`) are for
      lineage/lookup only and are never read for display. There's a
      structural test asserting those display columns stay VARCHAR, plus
      behavioural tests that rename a subject, recategorise it, and edit
      the policy's passing grade, then assert the frozen record is
      unchanged.
      **`app/academic_record_service.py`** — `capture_academic_record()`
      reads the already-computed derived tables via `app/report_card.py`
      (so the §16 row order matches the screen and SF9) and never
      recomputes a grade. Wired into Grade Summary's **Finalize**, which
      is already gated on the record being COMPLETE (§23) — capture on an
      un-recomputed enrollment would freeze stale numbers. Re-finalizing
      after an audited reopen replaces the child rows and bumps
      `revision` rather than accumulating duplicates.
      A component keeps its own Final Grade in `component_final_grade`
      even though §16 blanks that cell on the printed card: the record
      holds the truth, the form decides what to show.
      Gotcha worth remembering: `session.flush()` must come **after** the
      NOT NULL columns are populated. Flushing straight after
      `session.add()` (to get the id for child rows) fires the INSERT with
      every field still None.
      `pytest tests/` is 147. Note the new tests hit the live database and
      roll back, so the suite now takes ~60s rather than ~10s.
- [x] Phase 12 (Temporary term cards) built; not yet clicked through by
      the user. **`app/term_card.py`** draws the end-of-term slip (§39)
      directly with ReportLab, **eight cards to a 8.5 × 13 in sheet**
      (2 across × 4 down) — the school prints on Philippine long bond, so
      that replaces §39's suggested "6 per landscape Letter". The school
      seal is on every card. One routine draws a card into an arbitrary
      rectangle and the page builder tiles it, the same shape as the
      certificate generator, so the single-learner print and the
      whole-section batch can't drift. Pagination is automatic (§39).
      **`app/admin_pages/term_cards.py`** is the page (Adviser
      own-section + Super Admin/Registrar): school year → section →
      **term**, an on-screen preview, "Print section" and "Print selected
      learner".
      **The rule worth not getting wrong here:** the card lists the Grade
      11 language pair as **two separate subjects**, via the new
      `report_card.build_term_subject_rows()`. That's the opposite of the
      annual report card, where §16 collapses the pair into one row — but
      it's right for this card, because §17 computes the Term Average
      printed at its foot from those two subjects. Collapsing them would
      print a list that doesn't add up to the figure beneath it.
      **LibreOffice was installed at the start of this phase**, so PDF
      export is now live everywhere (`is_pdf_available()` → True).
      `pytest tests/` is 162.
- [x] **Performance pass (post-Phase-12, user-reported "pages feel
      slow")** — measured, not guessed. The database is Supabase in Tokyo
      and the median round trip from here is **~85ms**, so the cost of a
      page is dominated by *how many* queries it issues, not by how much
      data they return.
      The problem was a classic N+1: `build_learning_area_rows` and the
      page loops issued **~12 queries per learner**. A 4-learner section
      took 4.8s; a real 40-learner section projected to ~480 queries and
      **~41 seconds**. Streamlit re-runs the whole script on every widget
      interaction, so that cost was per *click*, not per visit.
      **The fix is `report_card.ReportCardContext` + `load_report_context()`**
      — every per-enrollment fetch became one `IN (...)` over the whole
      roster, so loading a section costs a *fixed* 8 queries and building
      each learner's rows afterwards costs **zero**. 40 learners now cost
      the same round trips as 1 (~0.7s instead of ~41s). Callers that
      render a roster (Grade Summary's class summary and per-learner
      detail, Term Cards) load one context and pass it in; single-learner
      callers (SF9, the academic-record snapshot) can still omit it and
      get a context built for them.
      `tests/test_query_cost.py` locks this in by asserting the *shape*
      of the data access — a preloaded context must issue **zero**
      queries while rendering, and a whole section must cost no more than
      one learner. Those assertions stay meaningful on a fast local
      database, where the bug would otherwise be invisible.
      **Pool** (`app/database.py`): was SQLAlchemy's default 5 + 10, which
      would queue requests once ~15 teachers were active; now 20 + 10
      against the instance's `max_connections = 60`, leaving margin for
      the SQL editor and anything else. `pool_pre_ping=True` and a
      30-minute `pool_recycle` were added because Supabase closes idle
      connections server-side — without them the first query on a dropped
      connection raises instead of reconnecting, which shows up as random
      errors rather than at a predictable moment. `pool_timeout` is 10s so
      exhaustion fails fast and legibly instead of just being slow. All
      overridable via `DB_POOL_*` env vars.
      **Deliberately NOT done: caching reference data** (school, terms,
      sections) in `st.cache_data`. Measured first: it would save ~6 of
      the remaining 15 queries on a page, but caching ORM instances risks
      detached-instance and staleness bugs across 15 pages for a modest
      gain once the N+1 was gone. If pages still feel slow, that's the
      next lever — cache plain tuples for the dropdowns, not ORM objects.
- [x] Phase 13 (Excel import/export migration tooling) built; not yet
      clicked through by the user. **Scope was chosen with the user: the
      two import kinds that carry real migration volume — learners and
      term grades — not all six §51 lists.** Subject catalog already has
      the lightweight CSV path from Phase 6, and school info is a single
      row an admin types once. Exports cover the full §52 set, since they
      share one code path.
      **`app/import_pipeline.py`** is §51's sequence — upload, detect
      columns, map, validate, show errors, confirm, audit — parameterised
      by an `ImportSpec` so each kind supplies only its columns,
      validators and writer. Split so the DB-free half (reading, mapping,
      value parsing) is unit-testable without a session.
      **`app/import_specs.py`** holds the two kinds and covers every error
      §51 names: duplicate LRN (both against the database *and* within the
      same file), unknown section, unknown subject, invalid grade,
      impossible date, and a subject not offered during that term — the
      last being the one that matters most, since
      `section_subject_offerings` is the single source of truth for what a
      learner is graded on (rule 5).
      Two rules the importer holds to: a **blank grade stays blank**, never
      0 (rule 2); and imported grades land as **DRAFT**, never straight to
      FINALIZED, so migrated data still goes through the normal
      submit/verify workflow (rule 7). Re-importing the same
      learner/subject/term updates in place rather than duplicating.
      **`app/export_service.py`** does §52's five exports as .xlsx and
      .csv. **The LRN trap runs in both directions and is tested at both
      ends:** Excel hands a 12-digit LRN back as a *float*, so a naive
      `str()` on read yields `1.07041140016e+11` and destroys it; and on
      write, a numeric cell shows scientific notation and drops a leading
      zero. Reading coerces integral floats back to int-then-str, and
      writing sets both a string value *and* number_format `"@"` so Excel
      won't re-coerce on save. CSV quotes every field.
      Validation reference data is loaded **once per file**, not per row —
      the same ~85ms-per-round-trip lesson from the performance pass; a
      300-row masterlist validated per row would take half a minute.
      Pages: **Import from Excel** (Super Admin + Registrar only — it
      writes learner records) and **Export** (Adviser own-section too).
      A failed validation still writes an `import_jobs` row, since a
      rejected import is also something an administrator may need to
      account for.
      `pytest tests/` is 204.
- [x] Phase 14 (Audit logs, backups, security hardening) built; not yet
      clicked through by the user. `audit_logs` existed since Phase 1 but
      **nothing wrote to it**, so CLAUDE.md rule 8 was unmet until now.
      **`app/audit_service.py`** — `record()` appends an entry to the
      *caller's* session and deliberately does **not** commit: the entry
      belongs to the same transaction as the change it describes, so a
      change that rolls back leaves no misleading trail (this is what
      makes the offering-delete case correct — a delete refused by an
      `ON DELETE RESTRICT` FK rolls back its own audit row with it).
      Actions are module constants, not free strings, so the viewer can
      filter on them and a typo can't invent a new action.
      `REASON_REQUIRED` (reopen ×2, award override, calendar change)
      **raises** on a blank reason rather than writing a row that can't
      answer "why" — a page reaching that is a bug in the page.
      `jsonable()` exists because JSONB can't take the types that
      actually appear in a before/after pair: Decimal grades, dates,
      UUIDs, Enums. A whole Decimal becomes an `int` (93, not "93.00" —
      the DB column is `Numeric(5,2)` but grades are integral by
      construction), and **None stays None**, never the string "None" —
      rule 2 at the audit boundary.
      IP and user agent come from `st.context.ip_address` /
      `st.context.headers`, wrapped so a script, test or background job
      (no request to describe) records None rather than failing. Audit
      logging must never be why a legitimate change fails.
      **Wired into every change §50 enumerates**: grade created/changed/
      submitted (Gradebook), finalized/reopened (Grade Summary),
      attendance altered, month finalized/reopened (Attendance), learner
      movement (Enrollment), subject offering changed (Section
      Offerings), calendar day changed (Academic Calendar), award
      override set/cleared (**inside `award_service`, not the page**, so
      no caller can override an award without leaving an entry), user
      roles/active changed (Users & Roles), bulk import (Import).
      **The one deliberate omission: creating an attendance record isn't
      logged, only changing one.** §50 says "attendance altered after
      initial entry", and the "Prepare this month's sheet" button
      materialises a PRESENT row per learner × class day — logging those
      would bury every real correction under thousands of rows.
      **`app/admin_pages/audit_log.py`** (Super Admin only) — read-only
      viewer, filterable by category/action/user. There is no delete
      control, no edit control, and no "clear old entries" button
      anywhere; `audit_service` exposes no delete function at all. §50
      requires the history to outlive the people it records, and the
      structural guarantee is stronger than a permission check. A test
      asserts the module never grows one.
      **Session timeout (§53)** in `app/auth.py`: 60 minutes idle
      (`SESSION_TIMEOUT_MINUTES`), enforced in `get_current_user()` —
      which is the right hook precisely *because* Streamlit re-runs the
      whole script on every interaction, so it sees every action the user
      takes and can both check and refresh the window there. A timed-out
      session is cleared, not hidden, so the tokens go too; the login
      page then says why.
      **`app/backup_service.py` + `app/admin_pages/backup.py`** (§55) —
      one CSV per table in a zip, plus a `MANIFEST.txt` with row counts
      and restore steps. Files are numbered in
      `Base.metadata.sorted_tables` order, which SQLAlchemy sorts by FK
      dependency, so **restoring in file order never violates a foreign
      key** (and reverse order is the safe delete order). CSV rather
      than `pg_dump` because it needs no matching Postgres version and no
      binary on the operator's machine, and stays readable long after
      this app is gone. Downloading is audit-logged (§54 — a copy of
      every learner record leaving the system).
      **The thing to not misunderstand about that file: it is NOT a
      complete disaster-recovery backup.** Supabase's own automated
      backups are, and are what you restore from after a real failure —
      this dump can't see the `auth` schema, so it contains the school's
      records but **not its logins**. Both the page and the manifest say
      so, and a test asserts the manifest keeps saying so.
      Honest §53 status, since the section is a checklist and only part
      of it is application code: **done** — secure auth (Supabase, we
      never store or hash a password ourselves), RBAC, server-side
      permission checks (Streamlit is server-rendered; there is no client
      the checks could be moved to), safe ORM queries, no guessable
      report URLs (§54 — PDFs are generated in-process and streamed,
      never stored at a path), inactivity timeout, backup, audit trail.
      **Not done, and deliberately deferred to Phase 15 (deployment):**
      HTTPS termination, and rate limiting beyond what Supabase Auth
      already applies to login attempts. **Not applicable:** CSRF and
      secure-cookie hardening — the app holds session state server-side
      per websocket in `st.session_state` and sets no auth cookie, so
      there is no cookie-authenticated form post to forge.
      `pytest tests/` is 222.
- [x] **LibreOffice removed; SF2/SF9 render in pure Python** (post-Phase-14,
      user-directed after a deployment costing exercise). Measured, not
      assumed: `soffice` peaks at **261 MB RSS per conversion** and
      `xlsx_to_pdf` limited concurrency to nothing, so **three advisers
      downloading a PDF at once exceeded a 1 GB host** — an OOM restart
      signs everyone out and loses unsaved grade entry. It was also the
      only thing forcing OS-level packages into the deployment.
      **`app/xlsx_render.py`** replaces it. Verified by rendering SF9 and
      SF2 both ways and comparing, not by eye. **4.4× faster** (0.9s vs
      4.1s on SF2). `app/pdf_convert.py` is deleted.
      **Five traps found by that comparison**, each of which produced
      output that looked plausible:
      (1) **Column widths are stored as `<col min= max= width=>` ranges
      keyed by the first letter only.** SF9 has one entry under "P"
      governing columns 16-26; reading them per letter left eleven columns
      at the default width, inflating the card from 12.25in to 16.11in and
      shrinking the whole form to 65% to make it fit.
      (2) **`#` is an optional digit placeholder, `0` a required one.**
      Treating them alike printed SF2's No. column (`#.###`) as "1.000";
      ignoring number formats entirely printed its percentages as "1"
      instead of "100.00%".
      (3) **A `OneCellAnchor` carries its size in `ext`, not a `to`
      marker.** Falling back to the image's natural pixel size drew the
      school seal ~10in wide across the header.
      (4) **`fitToWidth`/`fitToHeight` are page *counts*, and 0 means
      "as many as needed".** SF2 is `fitToWidth=1, fitToHeight=0` — one
      page wide, unlimited tall — and hides unused learner rows, so a
      3-learner section renders short while a full one is 15.4 x 22.6in.
      Forcing one page meant 35% scale, printing 5pt text at 1.8pt.
      **Test with a full roster, never the seeded 3-learner section.**
      (5) **`_wrap` must preserve leading whitespace.** `COMPONENT_INDENT`
      is ten leading spaces and is what makes the G11 language pair read
      as two components of one parent area (§16); `text.split()` drops it.
      SF9's block-out for non-offered terms is now written as a **direct
      fill as well as** via the template's conditional formatting, because
      the renderer draws what a cell says rather than evaluating CF. This
      is **not** the Phase 10 bug — that was `PatternFill(fill_type=None)`
      to *clear* a fill, which serialises onto OOXML fill index 1, always
      gray125. Nothing clears; an offered term is left untouched, and the
      colour is read from the template's own rule (#595959) rather than
      hardcoded.
      **Batch print** ("Print the whole section" on the SF9 page) renders
      every learner's card into one PDF, a page each, warning about any
      learner whose record isn't COMPLETE (blank cells on a card going
      home read as missing grades). Building it exposed the N+1 one layer
      above `ReportCardContext`: **one SF9 costs ~43 queries**, ~3.6s at
      Tokyo latency, so 40 cards took over two minutes. **`Sf9BatchContext`
      / `load_sf9_context`** batches the school, calendar, offerings,
      summaries and movement windows — **43 queries per learner → 4**,
      and a full section is ~35s instead of ~170s. `tests/test_query_cost.py`
      locks the shape in.
      Fonts fall back to metric-compatible base-14 faces (Arial →
      Helvetica). Worth knowing: **the old pipeline substituted fonts
      too** — it only looked exact locally because Windows has Arial,
      Carlito and Aparajita installed; a Linux server substitutes either
      way. `register_font_file()` can embed real TTFs if that ever
      matters. **Verified in production on 2026-08-12**: SF9 and SF2
      generated on the deployed Linux host were checked by the user and
      look correct, so the substitution is a non-issue in practice and
      no TTFs need embedding.
      `pytest tests/` is 246.
- [x] **In-app Quick Guide** (`app/admin_pages/help.py`) — reachable by
      every role, and by a user with no role yet (it's the one page that
      explains what they're waiting for). Deliberately **not** a feature
      tour: every entry is a place where doing the natural thing produces
      a wrong record rather than an error — blank-is-not-zero, LATE/CUTTING
      still count as present, editing a SUBMITTED grade reverts it to
      DRAFT, "Prepare this month's sheet" must be re-run after a late
      enrollee, Section Offerings is the source of truth, the backup
      excludes login accounts. Content is data, not markup, so the
      sections a user needs show first.
      It also answers the question this codebase is most likely to
      generate: **why the G11 language pair shows no Final Grade on the
      report card but two separate rows on the term card** (§16 vs §17 —
      exact opposites, both correct).
      `tests/test_help.py` keeps it honest: role codes must exist, every
      role with screens must be covered, and both halves of the language
      rule must stay explained.
      **Surfaced while writing it:** `ATTENDANCE_ENCODER` and
      `SCHOOL_HEAD` were seeded roles with **no pages wired**, so granting
      either alone landed the user on "not authorized". SCHOOL_HEAD was
      built out immediately after (below); `ATTENDANCE_ENCODER` is still
      open, and the guide says to grant ADVISER instead meanwhile.
- [x] **School Head read-only view** (§3F: view dashboards, review section
      summaries, review finalized records, view/print reports, **cannot
      change official data**).
      **`AuthUser.is_read_only()`** is the single rule, and it is
      deliberately *positive* about who may edit — `EDITING_ROLES` lists
      the four working roles, and anything else (including a brand-new
      account with no roles) is read-only until someone decides otherwise.
      That is the safe direction to fail. The limit describes the
      **account, not the title**: a principal who also advises a section
      holds ADVISER too and edits normally.
      **`app/admin_pages/dashboard.py`** is the "view dashboards" half —
      enrolment and completion per section, grade-encoding progress per
      term, and which section-months of attendance are still unfinalized.
      Read-only *by construction*: it draws no control that writes, so
      there is nothing to forget to hide. Also given to SUPER_ADMIN and
      REGISTRAR, who want the same overview.
      A School Head additionally gets Grade Summary, SF9, SF2, Term Cards
      and Export — the report pages are safe by construction too (they
      only generate documents). **Grade Summary is the one exception**:
      it can write, so Recompute (single and section-wide) and the whole
      finalize/reopen block are gated on `is_read_only()`.
      Scoping note: a School Head is **not** adviser-scoped — §3F says
      review *section summaries*, plural — so the four report pages and
      Grade Summary all treat SCHOOL_HEAD like REGISTRAR for section
      visibility while still refusing every write.
      `tests/test_read_only_role.py` guards this structurally rather than
      by clicking: it parses each granted page's AST and asserts the
      purely-read-only ones call **none** of the known writing functions,
      that every page in the navigation actually admits the role, and
      that Grade Summary checks `is_read_only()` at least three times.
      `st.navigation` raises on a duplicate `url_path`, so
      `streamlit_app.py` now de-duplicates the page list — a principal
      holding SCHOOL_HEAD *and* REGISTRAR was getting the same pages
      twice.
      `pytest tests/` is 272.
- [x] **Deployment prep (Phase 15).** `docs/deployment.md` (first deploy)
      and `docs/operations.md` (updating, migrations, adding a template,
      adding a teacher, what to do when it breaks) — written for the ICT
      Coordinator rather than a developer, since that's who will run them
      while live.
      **`tests/test_deployment_contract.py` keeps the docs honest**: every
      env var the code reads must appear in `.env.example`, every required
      secret must appear in the deployment doc, no secret value may be
      committed, `.env` must stay gitignored, the entrypoint must stay at
      the repo root, and **no module under `app/` may import `subprocess`
      or mention `soffice`** — that last one is what stops the OS-level
      packaging problem creeping back after LibreOffice's removal.
      It caught one real drift immediately: `.env.example` documented
      `DB_POOL_MAX_OVERFLOW` where the code reads `DB_MAX_OVERFLOW`.
      **Backup memory fixed**: the page kept the whole zip in
      `st.session_state`, which pins it for the life of the session. It
      now spills to a temp file and keeps only the path, deleting the
      previous file each time so copies of the entire database never
      accumulate.
      **Answered while here: `terms.submission_deadline` is stored but
      never read by any code.** Encoding is gated purely by the manual
      OPEN/CLOSED toggle, so a term can be reopened at any time past due
      and nothing ever auto-closes — nobody can be locked out by the
      calendar. Documented in the runbook, since "a teacher can't type a
      grade" will otherwise be diagnosed as a bug.
      `pytest tests/` is 299.
- [x] **SF4 (Monthly Learners' Movement and Attendance)** built — spec
      **§77**, appended at the end of `docs/master-spec.md` rather than
      inserted near SF2 (§34) so no existing section number shifted; the
      codebase references §NN everywhere. Added with the user's explicit
      approval — **never add scope to master-spec.md without asking.**
      `app/sf4_report.py`, `app/admin_pages/sf4.py`, **Excel only** (no
      PDF: it's submitted as a file, so the conversion would be dead
      weight). School-wide, one row per Track/Strand: rows 12-22 Grade 11,
      23 total, 24-34 Grade 12, 35 total, 36 grand total; every figure an
      M/F/T triple.
      **Why SF4 could be built while SF5 waits.** The official SHS forms
      are semester-shaped and this school runs three terms. SF4 reports a
      *month* — headcounts on the last class day, movements dated inside
      the month, attendance over the month's class days — none of which
      depends on how the year is divided, so nothing is reinterpreted.
      Its one period-shaped field, "Semester", carries the term the month
      falls in. **SF5-A and SF5-B are deliberately not built**: both have
      1st/2nd-semester summary tables that cannot be filled honestly from
      three terms, and the user is waiting on the division's updated
      form. Their templates are in `sf-templates/` ready for then.
      **The percentage trap resurfaced and was caught by inspecting the
      output, not by a test**: `Tally.total` sums male+female, right for
      counts and for a daily average, wrong for a percentage — 62.5% male
      and 100% female came out as 162.5%. Percentages now set
      `total_override` and recompute from the underlying days. SF2 hit
      exactly this and reported 200%.
      Cost is **11 queries flat** for the whole school (~1s), because
      every fetch is one `IN (...)` — a per-learner query here would be
      the worst offender in the app at 1,200 learners.
      `tests/test_sf4_report.py` pins the counting rules against plain
      collections (no database), including that a mid-month transfer-out
      is *not* registered at month end even though SF2 still lists them
      that month (§32), and that LATE/CUTTING count as present (§30).
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

## Development phases (per spec Section 71 — build in this order)

1. Architecture, DB, auth, permissions
2. School Year / Grade Level / Track / sections / subject catalog — done
3. Learners and enrollment — done
4. Subject profiles and section-specific subject offerings — done
5. Teacher assignments and term gradebooks — done
6. Grade computation engine (incl. combined-language rule) — done
7. Annual summary, validation, finalization, awards — done
8. Academic calendar and attendance — done
9. SF2 — done
10. SF9 — done
11. Temporary SF10 — record layer done; report BLOCKED on the template file
12. Temp cards and certificates — done
13. Excel import/export migration tooling — done (learners + term grades; §52 exports in full)
14. Audit logs, backups, security hardening — done (HTTPS + rate limiting
    deliberately left to 15; see the Phase 14 status entry)
15. Automated tests, deployment — **DEPLOYED 2026-08-12** to Streamlit
    Community Cloud from GitHub (branch `master`, entrypoint
    `streamlit_app.py`, secrets in the app's Settings panel). Pre-flight
    was clean: `.env` never in history, no schema drift (`alembic check`
    at `f00c90460cb9`), no OS-level dependencies.
    **The app is now live, so treat every change as a live change** —
    `git push` redeploys, a restart signs everyone out and loses unsaved
    grade entry, and migrations must follow the ordering in
    `docs/operations.md`. Deploy outside encoding hours.
    ← **Remaining before real use:** migrating the real ~1,200 learners
    (the database still holds 6 test learners), teacher accounts and
    assignments, and an end-to-end dress rehearsal on one section.
    Term 1 closes **15 September 2026**.
