# Build log — FGNMHS Grading System

The phase-by-phase record of what was built, in what order, and which bugs
were found along the way. Moved out of `CLAUDE.md` on 2026-08-13 because it
had grown to 85% of a file that loads into every session; the durable traps
it contains are summarised under "Traps already hit" in `CLAUDE.md`, and the
full story for any of them is here.

Read this when you need the history behind a decision. Nothing in it is
required to make a routine change.

Note: test counts quoted in these entries ("`pytest tests/` is 246") were
true at the time of writing and are not maintained — run the suite for the
current number.

---

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
- [x] **School Head read-only view** (§3E — numbered §3F when this was
      written: view dashboards, review section summaries, review
      finalized records, view/print reports, **cannot change official
      data**).
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
      Scoping note: a School Head is **not** adviser-scoped — §3E says
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
- [x] **`server_default="now()"` froze fourteen timestamps** (found
      2026-08-13 from the Audit Log page, where seven entries days apart
      all showed one identical time). A **string** `server_default` is a
      literal SQL value, so the DDL went out as `DEFAULT 'now()'` and
      Postgres resolved it **once, when the migration ran**:
      `audit_logs.created_at DEFAULT '2026-08-10 04:31:28.755305'`.
      Nothing errored — the column was populated, non-null and plausible;
      only the *ordering* of two events was lost, which is exactly what
      rule 8 and §50 need it for. `TimestampMixin` in `app/models/base.py`
      was always right (`func.now()`), which is why every table inheriting
      it — enrollments, attendance_records, term_grades — escaped.
      Migration `b7c31a9d40e2` resets all fourteen with
      `ALTER COLUMN … SET DEFAULT now()` (catalogue-only, no table scan,
      safe on the live app). The existing rows keep their false
      timestamps: the true times were never recorded anywhere, and
      inventing plausible ones is worse than leaving them visibly
      identical. `tests/test_model_defaults.py` parses every model and
      fails on any `server_default` string containing `(`.
      **Note `now()` is Postgres's *transaction* start time**, so entries
      written in one transaction legitimately share a timestamp — which is
      correct, since `audit_service.record()` deliberately rides the
      caller's transaction.
      Also worth knowing while reading the audit log: **it looked capped
      at 7 rows and isn't** (the viewer pages at 100). Most of what an
      admin does early on simply isn't logged — §50 enumerates grade,
      attendance, movement, offering, calendar, award, role and import
      changes. Creating a section or a user is **not** logged, though
      *deleting* a user is; that asymmetry is unaudited scope, not a bug.
- [x] **Excel damage on upload is now undone rather than complained
      about** (2026-08-13). The Learner Masterlist "Bulk-add" carried a
      second, stricter copy of the import rules, and every way the two
      differed was a way to reject a file the Import page would have
      taken: headers had to match letter-for-letter (`Sex` failed where
      `sex` passed), the birthdate had to be ISO, and the LRN had to be
      manually formatted as text. It now runs on `LEARNER_IMPORT` through
      `import_pipeline`, so there is **one implementation** and the
      Section column works in both places.
      Two parsing rules were hardened at the source, so the main Import
      page gets them too:
      (1) **`detect_date_order()` decides d/m vs m/d once per column, not
      per row.** `03/04/2009` is genuinely two different days; a single
      unambiguous value elsewhere in the file (a `25/…` or a `…/25/`)
      settles it for every row, and only a wholly ambiguous column falls
      back to `DEFAULT_DATE_ORDER = "mdy"` (what the school's machines
      actually write). Contradictory evidence deliberately picks neither.
      ISO is always tried first and is never ambiguous.
      (2) **`parse_lrn()` un-does Excel's numeric coercion, but refuses a
      rounded value.** `107041140016.0` and `1.07041140016E+11` are
      recovered arithmetically. `1.07E+11` is **rejected** — it expands
      cleanly to `107000000000`, which is exactly twelve digits and passes
      every other check, so accepting it would silently invent an LRN the
      learner does not have. The guard is a significant-digit count
      against the expanded length; a test pins the tempting wrong answer.
- [x] **A CSV saved from Excel destroys the LRN, and that is not
      recoverable** (2026-08-13, from a real 22-row upload). Every row was
      refused with "lost its digits", which looked like the importer being
      strict and was actually the file being wrong: Excel writes a CSV from
      what is *displayed*, so `107041140016` goes out as `1.07041E+11` and
      six digits cease to exist.
      **The proof that accepting it would be worse than refusing it**: in
      that file `1.07023E+11` appeared twice, `1.07028E+11` twice,
      `1.07043E+11` twice and `1.0703E+11` twice — eight distinct learners
      collapsed into four collisions. Expanding them would have reported
      **duplicates that do not exist**, rejected one real learner of each
      pair, and stored a fabricated LRN for the other. `parse_lrn`'s
      significant-digit guard is what stops that; the message now names the
      actual fix (**re-save as .xlsx**) instead of the fiddly one.
      **`.xlsx` has no such problem** — openpyxl reads the underlying value,
      so a plain numeric cell arrives as `107041140016` with no formatting
      work by the teacher. Verified end-to-end: the same 22 rows as .xlsx
      validate 22/22, with `M`/`F` stored as `MALE`/`FEMALE` and
      `01/31/2006` read as 2006-01-31.
      The Learner Masterlist caption had been claiming the opposite ("LRNs
      are read back correctly even if Excel has turned them into
      `1.07E+11`") — true of the float form, false of the rounded form, and
      the direct cause of the confusion. Fixed there and in the Quick Guide.
- [x] **Term-grade import was resolving section names across every school
      year** (found 2026-08-13 while reviewing it after the above).
      `validate_term_grades` keyed `sections` on the *name alone* over
      `session.query(Section).all()`. A section name is unique only per
      grade level per school year (`UniqueConstraint("school_year_id",
      "grade_level_id", "name")`), so the dict silently kept whichever row
      loaded last and a Term 1 grade could land on a same-named section
      from another year — a row that looks entirely normal and that no
      report will ever display. **This is the same class of bug the learner
      import already guarded against** with `_section_lookup`'s ambiguity
      set; term grades now reuse that helper.
      `TERM_GRADE_IMPORT.needs_school_year = True` is the fix, and it also
      answers the performance constraint: the unscoped version read
      **every enrollment and every `term_grades` row in the database** (at
      1,200 learners × 8 subjects × 3 terms that is ~29,000 rows per file,
      loaded as full ORM objects, twice — once in validate and again in
      commit). Now every lookup is filtered by year, `existing` selects two
      columns instead of whole objects, and `commit_term_grades` scopes to
      the enrollment ids actually in the file.
      **Also fixed: the import never refreshed the derived tables.** The
      Gradebook calls `recompute_enrollment_grades` after every save, so an
      import that skipped it left Grade Summary, term cards and SF9 blank
      for learners whose grades were already stored — a silent wrong state
      rather than an error. New `ImportSpec.after_commit` runs it, and
      exists because `recompute_enrollment_grades` **commits internally**
      and so cannot ride the import's own transaction. It is the slow half
      (a handful of round trips per learner), hence the progress bar and
      the "import one section at a time" advice on the page.
      Imported grades now also stamp `term_grades.source = "IMPORT"` (the
      column is free text and was never being set), so a migrated grade can
      be told from a typed one when reconciling year one against the old
      workbook.
      Sex needed no change — `_parse_sex` already accepted `M`/`F`/`MALE`/
      `FEMALE` in any case and returns the `Sex` enum, which persists as
      `MALE`/`FEMALE`; there are now tests pinning that.
- [x] **Bulk-add users from .xlsx** (2026-08-16). Email / Full Name / Roles,
      on the Users page rather than the Import page. `app/user_import.py`
      holds the columns and the checks; `provision_users` in
      `app/user_provisioning.py` does the work.
      **Not an `ImportSpec`.** Every spec commits inside the page's own
      transaction, and creating a user is half a remote call to Supabase
      Auth that cannot be rolled back — dressing that up as atomic would
      misreport a partial failure. Registering one would also have needed a
      new `import_job_type` enum value, i.e. an Alembic enum migration
      against a live database, for a run the audit log already records. The
      header-matching (`ColumnSpec`, `suggest_mapping`, `missing_required`,
      `read_table`) is reused; only the transaction shape differs.
      **An address that already has an account is skipped, not reset.**
      `provision_user`'s existing behaviour for a known email is to issue a
      fresh temporary password, which is right for one deliberate click on
      one person. Applied to a re-uploaded file it would invalidate the
      password of every teacher listed, mid-term, for no reason any of them
      could see. Skipping is reported on screen, and the per-user **Reset
      password** button remains the way to mean it.
      **One `admin.list_users()` for the file, not one per row.** That call
      returns every account in the school; looping over `provision_user`
      would have made a 40-teacher file 40 full listings plus a session and
      a role lookup each. The remote half runs first and to completion
      (each failure recorded, the rest continuing), then the whole database
      half — user rows, role grants, audit entries — is a single
      transaction with one flush. `tests/test_user_import.py` fails if
      `list_users` ever appears inside a loop there.
      **Validation is pure and takes no session.** The page already loads
      the roles and the user list to draw itself and hands both in, so a
      file is checked with zero extra round trips — which matters because
      Streamlit re-runs the page on every click with the upload still in
      hand.
      Passwords are shown once as a copyable block, never a download: a
      spreadsheet of live passwords sitting in Downloads is the thing the
      one-at-a-time flow was already avoiding. The blank template ships
      with **headers only** — a template carrying a worked example gets
      uploaded with the example still in it, and the school acquires an
      account for a person who does not exist.
      Tested through Streamlit's own runtime as well as by unit: `AppTest`
      drives the upload, the preview and the confirm button with the
      uploader and the provisioner stubbed, so a page that raises on the
      rerun after the click cannot pass.
- [x] **ATTENDANCE_ENCODER removed** (2026-08-16, at the school's request —
      the advisers encode their own section's attendance).
      It was only ever a dead entry in the role pickers: seeded so it
      *could* be granted, never in `EDITING_ROLES`, named by no page's
      `require_role`, and given no screens. Granting it on its own
      produced an account that could sign in and reach nothing, which the
      quick guide carried a standing warning about. Adding the bulk-add
      users column, where it would have appeared as a valid role code in
      the template's example, is what prompted finally deleting it.
      Removed from `app/seed.py`, the guide entry in
      `app/admin_pages/help.py`, the `EDITING_ROLES` comment in
      `app/auth.py`, and both docs. `tests/test_help.py` had an explicit
      `WITHOUT_SCREENS` exemption for it; that is gone, so
      `test_every_role_with_screens_is_documented` now asserts plain
      equality and a future role added without a guide entry fails.
      **The `roles` row needs the migration** (`d41f7a2c9e50`) — dropping
      it from the seed list alone leaves the row in the live database and
      therefore in every picker, since those read the table. Data only, no
      DDL: it deletes the role's `user_roles` and `role_permissions` rows
      first (every FK here is ON DELETE RESTRICT, so one stray row aborts
      it) and then the role. Deleting grants is safe to do silently
      *because* the role conferred nothing — it cannot narrow anyone's
      access — and is expected to affect zero rows.
      Destructive-class per `docs/operations.md`, so the order is code
      first, then `alembic upgrade head`. The app copes with the row
      present or absent either way, so the in-between state is harmless.
      **`docs/master-spec.md` §3E withdrawn too**, on a second explicit
      instruction — the spec is the source of truth and edits to it are
      asked for, not assumed, so it went in its own commit after the code.
      It was first left as a tombstone (`## E. *(withdrawn)*`) to avoid
      moving the School Head, then renumbered on a third instruction: **the
      School Head is now §3E, not §3F.** Ten citations moved with it —
      `dashboard.py`, `grade_summary.py` (three), `auth.py` (two),
      `test_dashboard.py`, `test_read_only_role.py`, and two historical
      entries in this file, which now name both numbers so an old quote
      still resolves. `docs/schema.md` says outright that anything citing
      §3F predates the change.
      **A renumbered spec section is the failure mode to watch for here**:
      every one of those references still *resolved* after the letters
      moved, they just resolved to a section about a different role. That
      is worse than a dangling link, which at least announces itself —
      hence changing them in one commit with the spec rather than as they
      are noticed.
- [x] **First-login password gate, and `last_login_at` actually written**
      (2026-08-16). Asked for after establishing that a temporary password
      never expires: `admin.create_user({"password": ...})` sets an
      ordinary password, Supabase expires *links* not passwords, and
      `change_password_form` was a voluntary sidebar expander nothing
      gated on. So a "temporary" password was permanent unless its holder
      chose otherwise — and since an admin generated it, read it, and
      relayed it by hand, every §50 entry naming that user was worth only
      as much as a shared secret.
      **Where the check runs is the whole design.** `require_role` is
      called at the top of every page and Streamlit re-runs the entire
      script on every widget interaction, so a query there would put ~85ms
      on *every click in the app* — the most expensive place in this
      codebase to put one. The flag is resolved **once, at login**, inside
      the session `_load_or_provision_user` already opens, and carried on
      `AuthUser.must_change_password`; enforcement is an attribute read.
      `tests/test_password_gate.py` walks the AST of `require_role` and
      fails on any `.query`/`.get`/`.execute` in it.
      `last_login_at` is stamped in the same statement. The column had
      existed since the initial migration with nothing ever writing to it,
      so "has this account ever been used?" was unanswerable in-app.
      **The backfill rule is `auth.users.last_sign_in_at IS NOT NULL`.**
      The gate reads NULL as "must change", so backfilling nobody would
      have locked every existing account — the only Super Admin included —
      out of a live system mid-term, and backfilling everybody would have
      exempted the accounts it was built for. Supabase already tracks
      whether an account has ever been used, which settles it without
      hardcoding a name or a date: signed in ⇒ had the chance to choose
      their own ⇒ compliant. Run against live it marked the 6 established
      accounts compliant and gated all 39 staff accounts created that day,
      none of which had been signed into yet.
      Two smaller pieces: `reset_password` sets `password_changed_at` back
      to NULL, so "reset" means "and they must choose a new one" rather
      than handing out a fresh permanent shared secret; and the Users page
      shows both facts per account (🔑 in the header, a caption reading
      "never signed in / still on the temporary password"), read off the
      row the page already loads, so no query per panel.
      Additive migration (`e2b6c1f4a733`), so per `docs/operations.md` it
      went **before** the code — and had to: the model carrying a column
      the database lacked failed nine tests that touch a real database.
- [x] **The Add User form could silently invalidate a password already
      handed out** (found 2026-08-16 by a sign-in that failed on a
      brand-new account).
      Symptom: "Invalid login credentials" for an account that was
      confirmed, unbanned, undeleted and held a password hash.
      Diagnosis, all read-only: `auth.users` said created 04:22:44,
      **updated 04:23:24** — something changed the account 40 seconds
      after making it. `SELECT encrypted_password = crypt(<the password
      shown>, encrypted_password)` returned **false**, so the password on
      screen was not the account's. And `public.users.updated_at` had
      never moved, which rules out `reset_password` (that writes to the
      app row) and leaves a second `provision_user` — the Create form ran
      twice.
      **Why pressing it twice was the natural thing to do.** The temporary
      password renders at the *top* of the page; the form sits at the
      bottom, below every user panel. Pressing Create changed nothing
      visible from where the button is. And `st.form` keeps its values in
      the browser, so the email was still sitting in the box. Meanwhile
      `provision_user` resets an address it already knows — deliberately,
      so a lost password is recoverable — so the second press issued a new
      password and quietly retired the one the admin had written down.
      Same shape as the trap already recorded for other add forms: "every
      add form sits below the list it adds to, so the top of the page is
      scrolled off by the time the button is pressed". The Users form had
      never been given `flash`/`render_flashes` or the field clearing.
      Fixed with the machinery that already exists for exactly this:
      `text_field` + `clear_text_fields("add_user")` (a **new widget key**,
      not a deleted session_state entry — see the trap on why the wrong
      version passes its tests), then `st.rerun()`, plus a flash so a
      toast appears where the click happened. A second press now hits
      "Email and full name are required" instead of re-provisioning.
      `tests/test_user_import.py` drives the real Streamlit runtime for
      the double-press, and separately asserts the page itself uses
      `text_field`/`clear_text_fields`/`st.rerun` — the script test alone
      would keep passing if the page drifted back to `st.text_input`.
- [x] **Creating an account and issuing a password were not audit-logged**
      (2026-08-16, spotted by the Super Admin who went looking for the
      user they had just made and found nothing).
      Rule 8 and §50 both. `provision_user` and `reset_password` had never
      written an entry — only the bulk path did, because it was written
      after the rule had been re-read. So an account could appear in the
      system with nothing anywhere saying who created it, and a password
      could be issued for somebody else's account leaving no trace.
      **Two new actions rather than reusing `USER_ROLES_CHANGED`.** Filing
      a password reset as a role change would put a claim in the log that
      is not true — it alters no role — and someone auditing how a person
      gained access would be reading noise. `USER_CREATED` and
      `USER_PASSWORD_RESET` join it in a new **Accounts** group on the
      Audit Log page, since "who can get into this system, and who gave
      them the password" is a question asked on its own rather than while
      looking through calendar edits.
      The bulk path's entries moved from `USER_ROLES_CHANGED` to
      `USER_CREATED` for the same reason.
      `actor_user_id` is threaded through both functions and is optional,
      because `scripts/bootstrap_admin.py` creates the first Super Admin
      when there is nobody to attribute it to — that entry records the
      account with a null actor rather than not existing at all.
      **The password itself is never recorded.** `tests/test_password_gate.py`
      parses each `audit_service.record(...)` call and fails if any keyword
      references the password — checked against the parsed call rather than
      the source text, because all three functions legitimately mention it
      further down when returning it to the caller that displays it.
- [x] **An adviser may hold more than one section** (2026-08-16, found
      while encoding sections for the new year).
      The school runs SNED sections — 4 in Grade 11, 3 in Grade 12 — and
      one Grade 11 adviser holds two of them: same strand, same subjects,
      same room, 5 and 7 learners. `uq_sections_adviser_per_school_year`
      refused it.
      **The rule had no source.** The migration that added it
      (`14e55ba4624b`) is bare autogenerate, "please adjust!" comment
      included, and the model comment above the index justified only its
      *scoping* — per year, partial — never the rule itself. §3C says an
      adviser sees learners in assigned **sections**, plural. And the
      application was already written for it: every adviser lookup is a
      `filter_by` returning a list, `section_picker` renders a *dropdown*
      of an adviser's sections, `delete_user` already says "still advises
      N section(s)", and nothing anywhere calls `.one()`. One index
      disagreed with the spec, the code and the school.
      Two alternatives were proposed and declined: a per-user "may advise
      several" flag (a column, a UI, a permission to maintain, and it must
      be set *before* the assignment — friction at exactly the wrong
      moment), and tags on sections exempting them (a concept the system
      lacks, plus a conditional partial index, where "tagged" comes to
      mean "exempt from a rule" rather than describing the section). Both
      build machinery to preserve a rule with no origin.
      **Checked before dropping, not after:** awards are computed from
      thresholds on the general average, never ranked within a section, so
      splitting 12 learners into 5 and 7 changes nobody's result; and
      `uq_teacher_assignments_active_offering` constrains one active
      teacher *per offering*, not one offering per teacher, so the same
      subject teacher covers both sections without a second wall. The real
      cost is operational and was stated plainly: each section needs its
      own Section Subject Offerings (rule 5 — seed **both**, or one
      section's gradebook is silently empty), and SF2/attendance
      finalization is two forms a month rather than one.
      **A warning replaces the refusal.** Picking the wrong name from a
      dropdown of forty is a real mistake and was the one thing the index
      caught. The Sections page now names the other sections the chosen
      adviser holds and says "That's allowed — check it's the name you
      meant." The adviser picker **moved outside `st.form`** to make that
      work: a form only reruns on submit, so the warning would otherwise
      arrive one click late, after the save it existed to question. Track
      already lived outside the form for the same reason.
      `tests/test_adviser_sections.py` pins the absent constraint, the
      warning's wording and scope, that a section isn't reported against
      itself, that the picker is outside the form (walked from the AST),
      and that the check costs no query.
      **Two bugs the warning itself introduced**, both reported within
      minutes of it going live, both caused by moving the adviser picker
      outside `st.form`:
      1. *Add a section with an adviser, save, and it warned that they
         already advise a section* — naming the one just created, on that
         teacher's first ever assignment. The picker kept its value across
         the rerun while `sections` was re-queried and now contained the
         new row. **A warning that fires on its own result is how people
         learn to ignore warnings.** Add now resets the picker, by the
         generation-in-the-key mechanism `_helpers.clear_text_fields` uses
         — not by deleting a `session_state` entry, which this repo has
         already shipped once and which clears the server's copy while the
         browser re-sends the old one. (`clear_text_fields` itself still
         leaves pickers alone on purpose: the next section usually wants
         the same track and a *different* adviser, so the adviser opts in
         explicitly.) The reset sits inside the `try_commit` success
         branch, so a failed add doesn't blank a choice the user has to
         retype.
      2. *Choosing an adviser on a second section collapsed the panel*, so
         the warning had to be hunted for by reopening it — and the Save
         button went with it. `st.expander` has no memory: every rerun
         rebuilds it closed, and a picker outside a form reruns on every
         change, which is the whole point of having moved it. The panel is
         now held open exactly while the picker disagrees with the
         database, so it closes by itself after Save. `_UNSET` distinguishes
         "nothing chosen yet" from "— none —", because clearing an adviser
         is a real edit and a truthiness check would shut the panel on the
         way to saving it.
      Same family as the `stateful_tabs` workaround: **a Streamlit
      container's open state does not survive a rerun**, and anything that
      causes reruns mid-edit has to restore it.
- [x] **Audit: six expanders collapsed on their own widgets** (2026-08-16,
      after the two Sections reports — "check if there are other
      collapsing problems").
      **`st.expander` has no memory.** Every rerun rebuilds it closed, and
      any widget *outside* an `st.form` reruns the script the moment it
      changes. So a picker, tick box or file uploader sitting directly in
      an expander slams its own panel shut, taking with it whatever it
      just produced and the button underneath. An AST sweep for
      "rerunning widget inside an expander but outside a form" found six:
      - **Sections** — the fix shipped hours earlier only covered the
        Adviser picker. **Track sits outside the form too** (it always
        has, so the strand list can cascade) and collapsed the panel just
        the same. The first fix was a dirty-check on one widget; it is now
        the shared mechanism, on both.
      - **Users** — changing **Roles** collapsed the panel and took the
        *Save roles* button with it, and ticking **I'm sure** collapsed it
        before *Delete* could be reached. Same page worked in all session
        and never noticed.
      - **Learners** and **Subject Catalog** — the worst two: the entire
        import flow (preview, errors, confirm) renders *inside* the
        expander, so uploading a file collapsed all of it and read as
        nothing having happened.
      - **Awards** — four certificate fields, each rerunning on blur, so
        filling the form closed the panel between fields.
      - **Subject Profiles** — the Track picker, as on Sections.
      One mechanism replaces the per-widget fix: `_helpers.panel_is_open`
      / `keep_panel_open`, paired as `expanded=panel_is_open(id)` on the
      expander and `on_change=keep_panel_open, args=(id,)` on each live
      widget. Only one panel is held open at a time — the one being typed
      in. Same family as `stateful_tabs`, and for the same reason: a
      Streamlit container's open state does not survive a rerun.
      `tests/test_expander_state.py` is the audit itself, kept: it walks
      every page's AST and fails on any expander holding a live widget
      without the pair. **Both halves were checked non-vacuous** — replayed
      against the pre-fix files from git it flags all six, and the runtime
      half (AppTest, driving the real Streamlit) shows a panel collapsing
      on its own change when the callback is removed and staying open when
      it is there. Structural tests alone would not have shown that; this
      session had already been bitten by exactly that gap.
- [x] **An adviser can bulk-enrol their own class from the Section
      column** (2026-08-17). The Learner Masterlist bulk-add already read
      a Section column and enrolled in the same step, but only for a
      Registrar or Super Admin: an adviser got a warning, the column was
      dropped, and the learners were created unenrolled — so the one
      person who actually types their own class list had to go and repeat
      it on the Enrollment page. Import from Excel is Registrar-only, so
      this panel is the only bulk route an adviser has at all.
      `validate_learners` now takes an **`adviser_user_id`**, which the
      page passes for an adviser and leaves None for a Registrar. Three
      choices in it are the whole design:
      1. **Refused per row, not per column.** A row naming someone else's
         section errors ("*JOBS is not one of your sections*") and the
         rest of the file still imports. Dropping the column instead —
         the old behaviour — would have created every learner
         unenrolled and said so only in a banner above a preview that
         looked entirely correct.
      2. **Sections they don't advise stay in the lookup.** Filtering the
         query to their own would have reported "unknown section" for a
         name that exists and is spelled right, sending a teacher to hunt
         a typo that isn't there.
      3. **An adviser settles the same-name tie-break.** A section name is
         unique only per grade level, so `_section_lookup` refuses a name
         held by two grade levels rather than guessing. An adviser holding
         exactly one of the two has named it unambiguously, since the
         other is refused to them anyway — so the narrowing happens before
         the ambiguity check, and only for them.
      Advising no section that year still drops the column with a warning:
      refusing every row would block the learners from being created at
      all, which is worse than not enrolling them. The panel names the
      sections the adviser can enrol into, so a refused row reads as a
      wrong section rather than a broken page.
      Four tests in `tests/test_import_export.py`, including the
      registrar path staying unscoped and the two-grade-level case
      resolving one way for an adviser and refusing for a Registrar.
