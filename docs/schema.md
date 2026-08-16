# FGNMHS Grading System — Phase 1 Database Schema (Field-Level)

Postgres (Supabase), designed for SQLAlchemy + Alembic. Source of truth for
business rules is `docs/master-spec.md` (section numbers below refer to it).

**Conventions, applied to every table unless noted:**
- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- Mutable operational rows (grades, attendance, enrollments, offerings, sections)
  also carry `version INTEGER NOT NULL DEFAULT 1` for optimistic concurrency (§46)
- `NULL` always means "not yet encoded" — never defaulted to `0` or `''` (§44, §65)
- Foreign keys use `ON DELETE RESTRICT` by default. Exception: any FK to
  `users` that records **who performed a logged action** (`created_by`,
  `encoded_by`, `submitted_by`, `verified_by`, `finalized_by`, `reopened_by`,
  `granted_by`, `generated_by`, `uploaded_by`, `requested_by`,
  `overridden_by`, `assigned_by`, `audit_logs.user_id`) uses
  `ON DELETE SET NULL` so a removed user account doesn't erase history. FKs
  to `users` that record a **current structural assignment** instead
  (`sections.adviser_user_id`, `teacher_assignments.teacher_user_id`) stay
  `RESTRICT` — deleting that user must force an explicit reassignment first.
- Enums are implemented as Postgres `ENUM` types unless flagged as `TEXT` with
  an app-level check, to keep additive changes cheap during early development

**Design decisions baked in:**
- **Snapshot, don't reference, for history** — finalized records capture their
  policy/subject data at that moment, not a live join
- **`section_subject_offerings` is the single source of truth** for what's
  graded (§48) — subject/track/strand defaults only seed it
- **Combined-language rule is data, not code** — via `combined_learning_areas`
  / `combined_learning_area_components` (§62)
- **Optimistic concurrency** via a `version` column on mutable rows (§46)
- **`programs` dropped** as a separate table (resolved open question — see
  Academic Structure below)
- **`learner_status_history` merged into `learner_movements`** (deviation
  from the spec's literal table list — see Learners & Enrollment below)
- **Grade entry is direct-only (Mode B)** — subject teachers type one
  official term grade per learner/subject/term; no in-app assessment-level
  breakdown (Mode A) for this pass — see Grades below

---

## 1. Organization

### `schools`
Singleton-ish (one row per physical school), modeled as a table rather than
config so identity-field changes flow through `audit_logs` (§4).

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| school_name | TEXT NOT NULL | |
| deped_school_id | TEXT NOT NULL UNIQUE | |
| region | TEXT NOT NULL | |
| schools_division | TEXT NOT NULL | |
| district | TEXT NOT NULL | |
| address | TEXT NOT NULL | |
| school_head_name | TEXT NOT NULL | |
| school_head_position | TEXT NOT NULL | |
| created_at, updated_at | TIMESTAMPTZ | |

### `school_years`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| school_id | UUID FK → schools | |
| name | TEXT NOT NULL UNIQUE | e.g. `"2026-2027"` |
| start_date, end_date | DATE NOT NULL | |
| recognition_date | DATE NULL | for award certificates, §40 |
| recognition_venue | TEXT NULL | |
| status | ENUM(`DRAFT`,`ACTIVE`,`ARCHIVED`) NOT NULL DEFAULT `DRAFT` | historical years stay viewable, §5 |
| created_at, updated_at | TIMESTAMPTZ | |

### `terms`
Dates are per-school-year, never hardcoded (§5). One row per term number
per school year.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| school_year_id | UUID FK → school_years NOT NULL | |
| term_number | SMALLINT NOT NULL CHECK (term_number IN (1,2,3)) | |
| name | TEXT NOT NULL | e.g. `"Term 1"` |
| start_date, end_date | DATE NOT NULL | |
| attendance_period_start, attendance_period_end | DATE NULL | |
| submission_deadline | TIMESTAMPTZ NULL | |
| grade_encoding_status | ENUM(`CLOSED`,`OPEN`) NOT NULL DEFAULT `CLOSED` | |
| finalization_state | ENUM(`NOT_STARTED`,`OPEN`,`FOR_REVIEW`,`FINALIZED`) NOT NULL DEFAULT `NOT_STARTED` | |
| created_at, updated_at | TIMESTAMPTZ | |

`UNIQUE (school_year_id, term_number)`

---

## 2. Users & RBAC

RBAC is enforced in the application layer against `users`/`roles`/`user_roles`
(per `CLAUDE.md`); `permissions`/`role_permissions` are an optional
admin-configurable refinement layer, not the primary gate.

### `users`
Never stores passwords — auth is delegated to Supabase Auth.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| supabase_auth_user_id | UUID NOT NULL UNIQUE | FK to Supabase Auth (not a DB-enforced FK — different system) |
| email | TEXT NOT NULL UNIQUE | |
| full_name | TEXT NOT NULL | |
| is_active | BOOLEAN NOT NULL DEFAULT true | |
| last_login_at | TIMESTAMPTZ NULL | Stamped by `_load_or_provision_user` on each sign-in. Nothing wrote to it before 2026-08-16, so NULL on an older account means "not since then", not "never" |
| password_changed_at | TIMESTAMPTZ NULL | NULL = still on the temporary password an admin issued, which is what arms the first-login gate. Read **once, at login**, never per render — see `AuthUser.must_change_password`. `reset_password` sets it back to NULL |
| created_at, updated_at | TIMESTAMPTZ | |

### `roles`
Seed rows for the 5 roles in §3: `SUPER_ADMIN`, `REGISTRAR`, `ADVISER`,
`SUBJECT_TEACHER`, `SCHOOL_HEAD`.

There were six. **`ATTENDANCE_ENCODER` was removed on 2026-08-16** — from
the seed list, from the `roles` table (revision `d41f7a2c9e50`), and from
the spec, whose §3E it had been. The school's advisers encode their own
section's attendance, so it was never granted and no screens were built
for it; it only ever appeared as a dead entry in the role pickers.
**§3E now means the School Head**, which moved up a letter — anything
citing §3F predates this.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| code | TEXT NOT NULL UNIQUE | |
| name | TEXT NOT NULL | |
| description | TEXT NULL | |

### `user_roles`
Global role grants. Fine-grained scope (which section, which subject) comes
from `sections.adviser_user_id` and `teacher_assignments`, not from this
table.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK → users NOT NULL | |
| role_id | UUID FK → roles NOT NULL | |
| granted_by_user_id | UUID FK → users NULL | |
| created_at | TIMESTAMPTZ | |

`UNIQUE (user_id, role_id)`

### `permissions`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| code | TEXT NOT NULL UNIQUE | e.g. `GRADE_REOPEN`, `AWARD_OVERRIDE` |
| description | TEXT NOT NULL | |

### `role_permissions`
| Column | Type | Notes |
|---|---|---|
| role_id | UUID FK → roles | |
| permission_id | UUID FK → permissions | |

`PRIMARY KEY (role_id, permission_id)`

---

## 3. Academic Structure

**Resolved open question:** `programs` is dropped. The spec uses
"Track/Program" and "Track/Program/Strand" interchangeably throughout
(`master-spec.md:252,1300,1427`), and its own example identifiers
(`G11-ASSH`, `G11-TECHPRO-CP`) show exactly two levels — Track, then one
child level whose label happens to differ by track ("Program/Cluster" for
Academic, "Strand/Specialization" for TechPro). Modeled as `tracks` →
`strands`, where `strands` covers both usages.

### `grade_levels`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| code | TEXT NOT NULL UNIQUE | `G11`, `G12` — new levels addable later, §6 |
| name | TEXT NOT NULL | |
| display_order | SMALLINT NOT NULL | |

### `tracks`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| code | TEXT NOT NULL UNIQUE | `ACADEMIC`, `TECHPRO` |
| name | TEXT NOT NULL | |
| display_order | SMALLINT NOT NULL | |
| is_active | BOOLEAN NOT NULL DEFAULT true | |

### `strands`
Replaces both `programs` and `strands` from the earlier summary.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| track_id | UUID FK → tracks NOT NULL | |
| code | TEXT NOT NULL | `ASSH`, `BE`, `STEM`, `ICT`, `HE`, `CP`, `EMS` |
| name | TEXT NOT NULL | full name, e.g. "Arts, Social Sciences, and Humanities" |
| display_order | SMALLINT NOT NULL | |
| is_active | BOOLEAN NOT NULL DEFAULT true | |

`UNIQUE (track_id, code)`

### `sections`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| school_year_id | UUID FK → school_years NOT NULL | |
| grade_level_id | UUID FK → grade_levels NOT NULL | |
| track_id | UUID FK → tracks NOT NULL | |
| strand_id | UUID FK → strands NOT NULL | |
| name | TEXT NOT NULL | e.g. "STEM - A" |
| adviser_user_id | UUID FK → users NULL | |
| room | TEXT NULL | |
| capacity | SMALLINT NULL | |
| display_order | SMALLINT NOT NULL DEFAULT 0 | |
| is_active | BOOLEAN NOT NULL DEFAULT true | |
| version | INTEGER NOT NULL DEFAULT 1 | |
| created_at, updated_at | TIMESTAMPTZ | |

`UNIQUE (school_year_id, grade_level_id, name)`. Partial unique index
`(school_year_id, adviser_user_id) WHERE adviser_user_id IS NOT NULL` —
an adviser is assigned to at most one section per school year (scoped by
year, not global, so the same person can advise a different section in a
later year); unassigned sections (`adviser_user_id` NULL) never collide.

---

## 4. Subjects & Grading Policy

### `subject_categories`
Seed rows: Core Subject; Other Academic Elective; Field Exposure/Arts
Apprenticeship/Creative Production & Innovation; Arts/Sports/Health and
Wellness; Research/Design and Innovation; TechPro Elective; Work Immersion.
The first two are a deliberate split from §9's single "Core / Other
Academic Elective" weight profile — that grouping only mattered while
grade entry used a weighted computation (Mode A), which this system
doesn't (§4, direct entry only). The term-offering distinction underneath
it still matters for reporting: **Core Subject** is offered and averaged
across all 3 terms; **Other Academic Elective** is offered in a single
term only, so its Final Grade is that one term's grade, not an average
(§18). Confirmed against real DepEd practice by the user.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| code | TEXT NOT NULL UNIQUE | |
| name | TEXT NOT NULL | |
| default_grading_policy_id | UUID FK → grading_policies NULL | |

### `grading_policies`
Logical, named policy family (one per category above, initially).

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| name | TEXT NOT NULL | |
| description | TEXT NULL | |
| is_active | BOOLEAN NOT NULL DEFAULT true | |

### `grading_policy_versions`
**Scoped down:** subject teachers encode the already-official term grade
directly (confirmed — no in-app Written Work/Performance Task/Exam
breakdown; that computation happens in the teacher's own external tools
before they type in the one final number). So this table no longer carries
weight percentages — it exists purely to version the **passing grade**
(§21) so a threshold change never mutates a finalized grade's basis (§59).
`written_work_pct`/`performance_task_pct`/`exam_pct` and the whole
`transmutation_tables` family (previously here for Mode A) are dropped. If
assessment-level entry is ever added back, both return as their own pass.

`school_years` has no `default_grading_policy_version_id` pointing back
here — that would form a genuine FK cycle with `effective_school_year_id`
below (`school_years` → `grading_policy_versions` → `school_years`), which
Postgres/Alembic can't order table creation around. "The default policy
for school year X" is resolved by querying `WHERE effective_school_year_id
= X AND status = 'ACTIVE'` instead (falling back to the latest version at
or before X if none is set for that exact year) — a query, not a stored
back-reference.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| grading_policy_id | UUID FK → grading_policies NOT NULL | |
| version_number | INTEGER NOT NULL | |
| effective_school_year_id | UUID FK → school_years NULL | first year this version applies |
| passing_grade | NUMERIC(5,2) NOT NULL DEFAULT 75 | never hardcoded elsewhere, §21 |
| min_grade | NUMERIC(5,2) NOT NULL DEFAULT 60 | |
| max_grade | NUMERIC(5,2) NOT NULL DEFAULT 100 | |
| status | ENUM(`DRAFT`,`ACTIVE`,`ARCHIVED`) NOT NULL DEFAULT `DRAFT` | |
| created_by_user_id | UUID FK → users NULL | |
| created_at | TIMESTAMPTZ | |

`UNIQUE (grading_policy_id, version_number)`

### `subjects`
Immutable-ID catalog; names are never used as keys (§8).

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| code | TEXT NOT NULL UNIQUE | |
| official_name | TEXT NOT NULL | |
| short_name | TEXT NOT NULL | |
| grade_level_id | UUID FK → grade_levels NOT NULL | |
| subject_category_id | UUID FK → subject_categories NOT NULL | |
| track_restriction_id | UUID FK → tracks NULL | NULL = offered under any track |
| default_grading_policy_id | UUID FK → grading_policies NULL | overrides category default |
| is_active | BOOLEAN NOT NULL DEFAULT true | |
| sort_order | SMALLINT NOT NULL DEFAULT 0 | |
| archived_at | TIMESTAMPTZ NULL | |
| created_at, updated_at | TIMESTAMPTZ | |

### `combined_learning_areas` / `combined_learning_area_components`
Models the Grade 11 Effective Communication / Mabisang Komunikasyon rule as
data (§14–16, §62) — the parent virtual learning area plus its component
subjects, so another combined rule could be added later without code
changes.

`combined_learning_areas`: `id`, `name` (e.g. "Effective Communication /
Mabisang Komunikasyon"), `grade_level_id` FK NOT NULL, `display_order`,
`is_active`.

`combined_learning_area_components`: `id`, `combined_learning_area_id` FK NOT NULL,
`subject_id` FK → subjects NOT NULL UNIQUE, `display_order`.
(`subject_id` UNIQUE — a subject belongs to at most one combined area.)

### `subject_profiles` / `subject_profile_subjects`
Per-track/strand default subject sets (§7, §12–13) — seed data only; the
actual per-section truth lives in `section_subject_offerings` (§48).

`subject_profiles`: `id`, `name` (e.g. "G11-ASSH"), `grade_level_id` FK NOT NULL,
`track_id` FK NOT NULL, `strand_id` FK NOT NULL, `is_active`, `created_at`, `updated_at`.

`subject_profile_subjects`: `id`, `subject_profile_id` FK NOT NULL, `subject_id` FK NOT NULL,
`term1_active BOOLEAN NOT NULL DEFAULT false`, `term2_active BOOLEAN NOT NULL DEFAULT false`,
`term3_active BOOLEAN NOT NULL DEFAULT false` (booleans, not a text code like `"111"`, §11),
`is_elective BOOLEAN NOT NULL DEFAULT false`, `display_order SMALLINT NOT NULL DEFAULT 0`.
`UNIQUE (subject_profile_id, subject_id)`.

### `section_subject_offerings`
**Single source of truth** for what a learner is actually graded on (§48).
Controls the gradebook, Term Average, Final Grade, General Average, SF9,
SF10, and temp cards — nothing else may substitute for it.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| school_year_id | UUID FK → school_years NOT NULL | |
| section_id | UUID FK → sections NOT NULL | |
| subject_id | UUID FK → subjects NOT NULL | |
| term_id | UUID FK → terms NOT NULL | |
| subject_category_id | UUID FK → subject_categories NOT NULL | snapshot/override of subject default |
| grading_policy_version_id | UUID FK → grading_policy_versions NULL | override; else resolved from subject/category |
| is_required | BOOLEAN NOT NULL DEFAULT true | |
| display_order | SMALLINT NOT NULL DEFAULT 0 | |
| status | ENUM(`PLACEHOLDER`,`CONFIRMED`) NOT NULL DEFAULT `PLACEHOLDER` | "Elective 2"/"Elective 3" style labels must be resolved before grading, §13 |
| version | INTEGER NOT NULL DEFAULT 1 | |
| created_at, updated_at | TIMESTAMPTZ | |

`UNIQUE (section_id, subject_id, term_id)`

### `teacher_assignments`
References the offering rather than embedding a teacher column directly
on it, so reassignment is itself an auditable event (§47).

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| section_subject_offering_id | UUID FK → section_subject_offerings NOT NULL | |
| teacher_user_id | UUID FK → users NOT NULL | |
| is_active | BOOLEAN NOT NULL DEFAULT true | |
| assigned_by_user_id | UUID FK → users NULL | |
| assigned_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| unassigned_at | TIMESTAMPTZ NULL | |

Partial unique index: `UNIQUE (section_subject_offering_id) WHERE is_active`
(only one active teacher per offering at a time).

---

## 5. Learners & Enrollment

### `learners`
Stable identity, independent of any single year's enrollment.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| lrn | TEXT NULL | 12-digit LRN, TEXT to preserve leading zeros (§26) |
| last_name, first_name | TEXT NOT NULL | |
| middle_name, extension_name | TEXT NULL | |
| sex | ENUM(`MALE`,`FEMALE`) NOT NULL | |
| birthdate | DATE NOT NULL | |
| created_at, updated_at | TIMESTAMPTZ | |

`CHECK (lrn IS NULL OR lrn ~ '^[0-9]{12}$')`
Unique partial index: `UNIQUE (lrn) WHERE lrn IS NOT NULL` (unique among
active records per §26; a soft-delete/merge flow can null it out on
duplicates without violating uniqueness). LRN is never placed in a URL —
an app-layer rule, not enforceable in schema.

### `learner_admission_records`
SHS-entry eligibility fields (§25) — one per learner, not duplicated per
enrollment, since they describe a single admission event rather than a
per-year fact.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| learner_id | UUID FK → learners NOT NULL UNIQUE | |
| date_of_shs_admission | DATE NULL | |
| high_school_completer | BOOLEAN NULL | |
| high_school_general_average | NUMERIC(5,2) NULL | |
| high_school_completion_date | DATE NULL | |
| junior_high_school_completer | BOOLEAN NULL | |
| junior_high_school_general_average | NUMERIC(5,2) NULL | |
| previous_school_name | TEXT NULL | |
| previous_school_address | TEXT NULL | |
| pept_passer | BOOLEAN NULL | |
| pept_rating | NUMERIC(5,2) NULL | |
| pept_examination_date | DATE NULL | |
| als_ae_passer | BOOLEAN NULL | |
| als_ae_rating | NUMERIC(5,2) NULL | |
| als_ae_examination_date | DATE NULL | |
| clc_name | TEXT NULL | |
| clc_address | TEXT NULL | |
| other_eligibility_notes | TEXT NULL | |
| created_at, updated_at | TIMESTAMPTZ | |

### `enrollments`
One row per learner per school year.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| learner_id | UUID FK → learners NOT NULL | |
| school_year_id | UUID FK → school_years NOT NULL | |
| grade_level_id | UUID FK → grade_levels NOT NULL | |
| section_id | UUID FK → sections NOT NULL | |
| enrollment_status | ENUM(`ENROLLED`,`LATE_ENROLLMENT`,`TRANSFERRED_IN`,`TRANSFERRED_OUT`,`NLS`,`DROPPED`,`SHIFTED_IN`,`SHIFTED_OUT`,`COMPLETED`,`GRADUATED`,`OTHER`) NOT NULL DEFAULT `ENROLLED` | denormalized "current status", kept in sync from the latest `learner_movements` row (§27) |
| derogatory_record | BOOLEAN NOT NULL DEFAULT false | |
| general_remarks | TEXT NULL | |
| term1_adviser_comment | TEXT NULL | |
| term2_adviser_comment | TEXT NULL | |
| term3_adviser_comment | TEXT NULL | |
| version | INTEGER NOT NULL DEFAULT 1 | |
| created_at, updated_at | TIMESTAMPTZ | |

`UNIQUE (learner_id, school_year_id)`. `track_id`/`strand_id` are not
duplicated here — they're read via `section_id → sections.track_id/strand_id`,
avoiding a second place those could drift out of sync.

### `learner_movements`
**Merged table** — the spec's summary lists `learner_status_history` (under
Learners) and `learner_movements` (under Attendance) as separate tables,
but their field sets are near-identical (status/type, effective date,
details, previous/receiving school, encoded-by, §27 and §32). Splitting
them would mean writing the same event to two tables. This single table
serves both purposes: it's the enrollment-status audit trail *and* the
source for SF2 monthly movement remarks. If two genuinely independent logs
were intended, split this back out before building the SF2 module.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| enrollment_id | UUID FK → enrollments NOT NULL | |
| movement_type | ENUM(same values as `enrollment_status` above) NOT NULL | |
| effective_date | DATE NOT NULL | |
| details | TEXT NULL | |
| previous_school | TEXT NULL | |
| receiving_school | TEXT NULL | |
| nls_reason | TEXT NULL | |
| remarks | TEXT NULL | |
| encoded_by_user_id | UUID FK → users NULL | |
| created_at | TIMESTAMPTZ | |

A transferred/dropped/NLS/shifted-out learner still appears in the
effective month's SF2 with a remark, then drops out of subsequent months
(§32) — this is a query concern (`effective_date` vs. report month), not
an extra schema construct.

---

## 6. Grades

**Scoped down (confirmed):** subject teachers only encode the already-
official term grade for their assigned learners/subject/term — Mode B
(§10) only, matching how the current Excel workbook actually operates.
There is no in-app assessment-level entry, so `assessment_categories`,
`assessments`, and `learner_scores` (Mode A) are dropped from this pass.
`term_grades.official_grade` is the single number a teacher types in per
learner per term; everything above it (Final Grade, combined-language
result, General Average) is still computed server-side and deterministic,
satisfying `CLAUDE.md`'s rule 1. If assessment-level entry is wanted later,
reintroduce those three tables and an `entry_mode` column on `term_grades`
in a dedicated pass rather than half-building it now.

### `term_grades`
The central mutable grade record.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| enrollment_id | UUID FK → enrollments NOT NULL | |
| section_subject_offering_id | UUID FK → section_subject_offerings NOT NULL | |
| term_id | UUID FK → terms NOT NULL | |
| official_grade | NUMERIC(5,2) NULL | the term grade that counts; NULL = not yet encoded (§44) |
| grading_policy_version_id | UUID FK → grading_policy_versions NULL | snapshot, for the passing-grade threshold used |
| source | TEXT NULL | e.g. "teacher's external e-class record" |
| import_batch_id | UUID FK → import_jobs NULL | set when bulk-imported |
| remarks | TEXT NULL | |
| status | ENUM(`DRAFT`,`SUBMITTED`,`VERIFIED`,`FINALIZED`) NOT NULL DEFAULT `DRAFT` | §45 |
| submitted_by_user_id, submitted_at | UUID FK → users NULL, TIMESTAMPTZ NULL | |
| verified_by_user_id, verified_at | UUID FK → users NULL, TIMESTAMPTZ NULL | |
| finalized_by_user_id, finalized_at | UUID FK → users NULL, TIMESTAMPTZ NULL | |
| version | INTEGER NOT NULL DEFAULT 1 | optimistic concurrency, §46 |
| created_at, updated_at | TIMESTAMPTZ | |

`UNIQUE (enrollment_id, section_subject_offering_id, term_id)`

Edits after `FINALIZED` are rejected at the app layer unless an explicit
reopen precedes them (tracked in `grade_finalization_records` below);
every value change is also written to `audit_logs` (§45, §50).

### `subject_final_grades`
Derived/cached per subject per school year — recomputed from `term_grades`
whenever a contributing term grade changes, never entered directly.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| enrollment_id | UUID FK → enrollments NOT NULL | |
| subject_id | UUID FK → subjects NOT NULL | |
| school_year_id | UUID FK → school_years NOT NULL | |
| final_grade | NUMERIC(5,2) NULL | NULL if any required term is missing |
| remark | ENUM(`PASSED`,`FAILED`,`INCOMPLETE`) NULL | |
| computed_at | TIMESTAMPTZ NULL | |
| version | INTEGER NOT NULL DEFAULT 1 | |

`UNIQUE (enrollment_id, subject_id, school_year_id)`

### `combined_learning_area_results`
Grade 11 combined-language results (§15–16) — the two component subjects'
own `subject_final_grades` rows exist but are excluded from SF9's Final
Grade cell and from the General Average; this table is what's actually
displayed/counted for the combined parent row.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| enrollment_id | UUID FK → enrollments NOT NULL | |
| combined_learning_area_id | UUID FK → combined_learning_areas NOT NULL | |
| school_year_id | UUID FK → school_years NOT NULL | |
| term1_combined, term2_combined, term3_combined | NUMERIC(5,2) NULL | |
| final_grade | NUMERIC(5,2) NULL | |
| remark | ENUM(`PASSED`,`FAILED`,`INCOMPLETE`) NULL | |
| computed_at | TIMESTAMPTZ NULL | |
| version | INTEGER NOT NULL DEFAULT 1 | |

`UNIQUE (enrollment_id, combined_learning_area_id, school_year_id)`

### `term_grade_summaries`
Per-term aggregate (§17 Term Average, §22 Term Completion Check) — the
term-level counterpart of `annual_grade_summaries`, and what a
`TERM`-scoped award policy is judged against.

**Note the deliberate asymmetry with `annual_grade_summaries`:**
`term_average` counts the Grade 11 combined-language components as **two
separate subjects**, because §17 says explicitly "Do not substitute the
combined language grade when calculating the Term Average". The General
Average below does the opposite, collapsing the pair into one virtual
learning area (§19). Same two subjects, two different treatments,
depending on which figure is being computed.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| enrollment_id | UUID FK → enrollments NOT NULL | |
| school_year_id | UUID FK → school_years NOT NULL | |
| term_id | UUID FK → terms NOT NULL | |
| term_average | NUMERIC(5,2) NULL | §17; NULL while any subject in the term is un-encoded |
| lowest_term_grade | NUMERIC(5,2) NULL | |
| failed_subject_count | SMALLINT NULL | |
| completion_status | ENUM(`COMPLETE`,`INCOMPLETE`) NOT NULL DEFAULT `INCOMPLETE` | |
| computed_at | TIMESTAMPTZ NULL | |
| version | INTEGER NOT NULL DEFAULT 1 | |

`UNIQUE (enrollment_id, term_id)`

### `annual_grade_summaries`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| enrollment_id | UUID FK → enrollments NOT NULL UNIQUE | one per enrollment (already one enrollment per learner-year) |
| school_year_id | UUID FK → school_years NOT NULL | |
| general_average | NUMERIC(5,2) NULL | never (T1+T2+T3)/3 — computed from applicable Final Grades, §61 |
| lowest_final_grade | NUMERIC(5,2) NULL | combined-language final counted once for G11, §19 |
| failed_subject_count | SMALLINT NULL | |
| completion_status | ENUM(`COMPLETE`,`INCOMPLETE`) NOT NULL DEFAULT `INCOMPLETE` | |
| computed_at | TIMESTAMPTZ NULL | |
| version | INTEGER NOT NULL DEFAULT 1 | |

### `grade_finalization_records`
Finalize/reopen workflow gate — distinct from `audit_logs` because it's
what the app *checks* to allow/deny edits, not just a record of what
happened.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| scope_type | ENUM(`TERM_SECTION_SUBJECT`,`ANNUAL_ENROLLMENT`,`ATTENDANCE_MONTH`) NOT NULL | |
| term_id | UUID FK → terms NULL | |
| section_subject_offering_id | UUID FK → section_subject_offerings NULL | |
| enrollment_id | UUID FK → enrollments NULL | |
| finalized_by_user_id | UUID FK → users NULL | ON DELETE SET NULL (who-performed-action field, per convention above) — nullability follows from that, not a claim finalization can happen anonymously; app layer requires it at write time |
| finalized_at | TIMESTAMPTZ NOT NULL | |
| reopened_by_user_id | UUID FK → users NULL | |
| reopened_at | TIMESTAMPTZ NULL | |
| reopen_reason | TEXT NULL | required by app logic whenever reopened_at is set |
| status | ENUM(`FINALIZED`,`REOPENED`) NOT NULL DEFAULT `FINALIZED` | |
| created_at | TIMESTAMPTZ | |

---

## 7. Attendance

### `academic_calendar_dates`
Per-date `term_id`, not inferred from month — required for the September
term split (§29).

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| school_year_id | UUID FK → school_years NOT NULL | |
| term_id | UUID FK → terms NULL | |
| calendar_date | DATE NOT NULL | |
| day_of_week | SMALLINT NOT NULL | |
| is_default_class_day | BOOLEAN NOT NULL DEFAULT false | |
| is_override | BOOLEAN NOT NULL DEFAULT false | |
| is_final_class_day | BOOLEAN NOT NULL DEFAULT false | |
| note | TEXT NULL | required whenever is_override, §28 |
| class_day_sequence | INTEGER NULL | |
| overridden_by_user_id | UUID FK → users NULL | |
| created_at, updated_at | TIMESTAMPTZ | |

`UNIQUE (school_year_id, calendar_date)`

### `attendance_records`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| enrollment_id | UUID FK → enrollments NOT NULL | |
| calendar_date_id | UUID FK → academic_calendar_dates NOT NULL | |
| status | ENUM(`PRESENT`,`ABSENT`,`LATE`,`CUTTING`) NOT NULL DEFAULT `PRESENT` | internal codes, not raw symbols; SF2 renderer maps to printed abbreviations (§30) |
| encoded_by_user_id | UUID FK → users NULL | |
| version | INTEGER NOT NULL DEFAULT 1 | |
| created_at, updated_at | TIMESTAMPTZ | |

`UNIQUE (enrollment_id, calendar_date_id)`

### `attendance_month_status`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| section_id | UUID FK → sections NOT NULL | |
| school_year_id | UUID FK → school_years NOT NULL | |
| year_month | DATE NOT NULL | first-of-month convention |
| status | ENUM(`NOT_STARTED`,`OPEN`,`FOR_REVIEW`,`FINALIZED`) NOT NULL DEFAULT `NOT_STARTED` | §33 |
| finalized_by_user_id, finalized_at | UUID FK → users NULL, TIMESTAMPTZ NULL | |
| reopened_by_user_id, reopened_at | UUID FK → users NULL, TIMESTAMPTZ NULL | |
| reopen_reason | TEXT NULL | |
| version | INTEGER NOT NULL DEFAULT 1 | |
| created_at, updated_at | TIMESTAMPTZ | |

`UNIQUE (section_id, year_month)`

---

## 8. Awards

### `award_policies` / `award_policy_versions`
Two selectable policies from §24 (Academic Excellence, legacy tiered
Honors) — never permanently merged. JSONB carries the tiered-Honors
structure since tier count/labels vary; flat columns cover the single-tier
Academic Excellence conditions.

`award_policies`: `id`, `name`, `description`, `is_active`.

`award_policy_versions`:

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| award_policy_id | UUID FK → award_policies NOT NULL | |
| version_number | INTEGER NOT NULL | |
| effective_school_year_id | UUID FK → school_years NOT NULL | |
| scope | ENUM(`TERM`,`ANNUAL`) NOT NULL DEFAULT `ANNUAL` | which average is judged, and how often — see note below |
| require_complete_record | BOOLEAN NOT NULL DEFAULT true | term completion for `TERM`, annual completion for `ANNUAL` |
| require_no_derogatory_record | BOOLEAN NOT NULL DEFAULT true | |
| min_general_average | NUMERIC(5,2) NULL | read against whichever average `scope` selects, despite the name |
| min_lowest_final_grade | NUMERIC(5,2) NULL | lowest Final Grade (`ANNUAL`) or lowest term grade (`TERM`) |
| require_no_failed_subject | BOOLEAN NOT NULL DEFAULT false | |
| tier_thresholds | JSONB NULL | e.g. `[{"label":"WITH HIGHEST HONORS","min_ga":98}, ...]` for the legacy tiered policy |
| status | ENUM(`DRAFT`,`ACTIVE`,`ARCHIVED`) NOT NULL DEFAULT `DRAFT` | |
| created_by_user_id | UUID FK → users NULL | |
| created_at | TIMESTAMPTZ | |

`UNIQUE (award_policy_id, version_number)`

**`scope` vs. tier shape are orthogonal.** `scope` decides *what average*
is judged and *how often*; whether `tier_thresholds` is set decides *how*
the threshold applies (flat minimum vs. highest-cleared-tier ladder).
Either scope works with either shape. As seeded:

- **Legacy Tiered Honors** — `scope=TERM`, tiered. Judged once per term
  against `term_grade_summaries.term_average` (§17), so a learner can
  make Honors in one term and miss it in another.
- **Academic Excellence (DO 15, s. 2026)** — `scope=ANNUAL`, flat.
  Judged once against `annual_grade_summaries.general_average` (§19/§20).

The tier dicts keep the key name `min_general_average` under every scope;
it's historical, and under a `TERM` scope it means "minimum Term Average".

### `learner_awards`
Stores the eligibility **reason**, not just a boolean — §24 explicitly
requires showing why a learner isn't eligible.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| enrollment_id | UUID FK → enrollments NOT NULL | |
| school_year_id | UUID FK → school_years NOT NULL | |
| term_id | UUID FK → terms NULL | set for a `TERM`-scoped policy, NULL for an `ANNUAL` one |
| award_policy_version_id | UUID FK → award_policy_versions NOT NULL | |
| award_result | ENUM(`ELIGIBLE_AWARDED`,`NOT_ELIGIBLE`) NOT NULL | |
| award_name | TEXT NULL | e.g. "ACADEMIC EXCELLENCE AWARD", "WITH HIGH HONORS" |
| reason | TEXT NOT NULL | eligibility explanation, always populated (§24) |
| is_override | BOOLEAN NOT NULL DEFAULT false | |
| override_by_user_id | UUID FK → users NULL | |
| override_reason | TEXT NULL | required whenever is_override (§40, §67) |
| computed_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| created_at | TIMESTAMPTZ | |

Uniqueness is "one row per enrollment per policy version per term",
enforced by **two partial unique indexes** rather than one constraint:

- `uq_learner_awards_term` on `(enrollment_id, award_policy_version_id,
  term_id) WHERE term_id IS NOT NULL`
- `uq_learner_awards_annual` on `(enrollment_id,
  award_policy_version_id) WHERE term_id IS NULL`

A single `UNIQUE (enrollment_id, award_policy_version_id, term_id)` would
**not** work: in SQL NULL never equals NULL, so it would happily admit
unlimited duplicate annual rows.

---

## 9. Reports

### `report_templates`
Versioned by type/effective date so the DB never needs restructuring when
a DepEd form layout changes (§56). `field_mapping` maps stored fields to
printable positions/components — the template, not the database, encodes
layout.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| report_type | ENUM(`SF2`,`SF9_G11`,`SF9_G12`,`SF10`,`TERM_CARD`,`CERTIFICATE`) NOT NULL | |
| name | TEXT NOT NULL | |
| version_number | INTEGER NOT NULL | |
| effective_date | DATE NOT NULL | |
| template_file_path | TEXT NOT NULL | Supabase Storage path |
| field_mapping | JSONB NOT NULL | |
| is_active | BOOLEAN NOT NULL DEFAULT true | |
| created_by_user_id | UUID FK → users NULL | |
| created_at | TIMESTAMPTZ | |

`UNIQUE (report_type, version_number)`

### `report_generation_logs`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| report_type | ENUM (same as above) NOT NULL | |
| report_template_id | UUID FK → report_templates NOT NULL | |
| generated_by_user_id | UUID FK → users NULL | ON DELETE SET NULL (who-performed-action field); app layer requires it at write time |
| scope | JSONB NOT NULL | filters used: school_year/section/term/learner/month |
| file_path | TEXT NULL | Supabase Storage path, if downloaded |
| readiness_status | ENUM(`READY`,`WARNING`,`BLOCKED`) NOT NULL | §66 |
| generated_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |

**Deferred:** `report_snapshots` (freezing the exact data behind a
generated PDF for provable reprint fidelity, §38) is intentionally not
designed in this pass — flagged in `CLAUDE.md` as needed before go-live,
not required for Phase 1. When it's built, it will likely sit alongside
`report_generation_logs`, storing a JSONB copy of the resolved report data
rather than relying on live joins that could drift if source records
change later.

---

## 10. Administration

### `audit_logs`
Append-only; the app layer must never expose an update/delete path for
this table (§50).

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK → users ON DELETE SET NULL | NULL for system-initiated actions |
| action | TEXT NOT NULL | e.g. `GRADE_FINALIZED`, `LEARNER_TRANSFERRED` |
| object_type | TEXT NOT NULL | e.g. `term_grades` |
| object_id | UUID NOT NULL | |
| previous_value | JSONB NULL | |
| new_value | JSONB NULL | |
| reason | TEXT NULL | required by app logic for sensitive actions |
| ip_address | INET NULL | |
| user_agent | TEXT NULL | |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |

### `import_jobs`
Preview → validate → confirm workflow (§51) — never a silent import.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| job_type | ENUM(`LEARNERS`,`SUBJECT_CATALOG`,`SUBJECT_PROFILES`,`TERM_GRADES`,`ATTENDANCE`,`SCHOOL_INFO`) NOT NULL | |
| status | ENUM(`UPLOADED`,`VALIDATING`,`VALIDATED`,`CONFIRMED`,`FAILED`) NOT NULL DEFAULT `UPLOADED` | |
| uploaded_by_user_id | UUID FK → users NULL | ON DELETE SET NULL (who-performed-action field); app layer requires it at write time |
| file_path | TEXT NOT NULL | Supabase Storage path |
| column_mapping | JSONB NULL | |
| validation_errors | JSONB NULL | e.g. duplicate LRN, unknown section |
| row_count | INTEGER NULL | |
| imported_count | INTEGER NULL | |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| confirmed_at | TIMESTAMPTZ NULL | |

### `export_jobs`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| export_type | TEXT NOT NULL | e.g. `SECTION_MASTERLIST`, `GRADEBOOK`, `ATTENDANCE` |
| requested_by_user_id | UUID FK → users NULL | ON DELETE SET NULL (who-performed-action field); app layer requires it at write time |
| scope | JSONB NOT NULL | |
| file_path | TEXT NULL | |
| status | ENUM(`PENDING`,`COMPLETE`,`FAILED`) NOT NULL DEFAULT `PENDING` | |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| completed_at | TIMESTAMPTZ NULL | |

**Not modeled:** a generic `configuration_versions` table is intentionally
skipped — every configurable concept already carries its own
version/effective-date columns (`grading_policy_versions`,
`transmutation_table_versions`, `award_policy_versions`, `report_templates`,
plus per-date `academic_calendar_dates` overrides), so a catch-all
versioning table would just duplicate that.

---

## Table index by domain

1. **Organization** — `schools`, `school_years`, `terms`
2. **Users & RBAC** — `users`, `roles`, `user_roles`, `permissions`, `role_permissions`
3. **Academic Structure** — `grade_levels`, `tracks`, `strands`, `sections`
4. **Subjects & Grading Policy** — `subject_categories`, `grading_policies`/`_versions`,
   `subjects`, `combined_learning_areas`/`_components`,
   `subject_profiles`/`subject_profile_subjects`, `section_subject_offerings`,
   `teacher_assignments`
5. **Learners & Enrollment** — `learners`, `learner_admission_records`, `enrollments`,
   `learner_movements`
6. **Grades** — `term_grades`, `subject_final_grades`, `combined_learning_area_results`,
   `annual_grade_summaries`, `grade_finalization_records`
7. **Attendance** — `academic_calendar_dates`, `attendance_records`, `attendance_month_status`
8. **Awards** — `award_policies`/`_versions`, `learner_awards`
9. **Reports** — `report_templates`, `report_generation_logs` *(`report_snapshots` deferred)*
10. **Administration** — `audit_logs`, `import_jobs`, `export_jobs`

42 tables total. Notably lighter than the original per-domain estimate in
two places: `programs` dropped (Academic Structure), and the
assessment-level Mode A tables (`assessment_categories`, `assessments`,
`learner_scores`) plus the `transmutation_tables` family dropped (Subjects
& Grading Policy) since grade entry is direct-only (confirmed). Slightly
heavier elsewhere: `permissions`/`role_permissions` added (Users & RBAC),
`grading_policies` split from its `_versions` table (Subjects & Grading
Policy).

**Implemented:** SQLAlchemy models live in `app/models/` (one file per
domain below) and the first Alembic migration is in `alembic/versions/` —
applied to the live Supabase DB, all 42 tables confirmed, `alembic check`
reports no drift. One design fix surfaced during implementation:
`school_years.default_grading_policy_version_id` was removed — it formed
a genuine FK cycle with `grading_policy_versions.effective_school_year_id`
that Postgres/Alembic can't order table creation around (see the
`grading_policy_versions` note above for the resolution).

Next: Phase 2 — School Year / Grade Level / Track / sections / subject
catalog (seed data + admin CRUD), per `master-spec.md` Section 71.
