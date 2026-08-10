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
  with `openpyxl` (preserves exact cell layout), then convert to PDF via
  headless LibreOffice (`soffice --headless --convert-to pdf`). Award
  certificates and temp cards use a custom template (python-docx or
  ReportLab).

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
- [ ] Not yet modeled: `report_snapshots` (freeze data behind a generated
      PDF for provable reprint fidelity) — add before go-live, not required
      for Phase 1.
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
- [ ] Next: Phase 9 (SF2) — ask before starting. `roster_for_month()` in
      `app/attendance_service.py` already sorts male-then-female
      alphabetically the way SF2 wants (§34), and the attendance data it
      needs is all in place.

## Development phases (per spec Section 71 — build in this order)

1. Architecture, DB, auth, permissions
2. School Year / Grade Level / Track / sections / subject catalog — done
3. Learners and enrollment — done
4. Subject profiles and section-specific subject offerings — done
5. Teacher assignments and term gradebooks — done
6. Grade computation engine (incl. combined-language rule) — done
7. Annual summary, validation, finalization, awards — done
8. Academic calendar and attendance — done
9. SF2 ← **we are here**
10. SF9
11. Temporary SF10
12. Temp cards and certificates
13. Excel import/export migration tooling
14. Audit logs, backups, security hardening
15. Automated tests, deployment
