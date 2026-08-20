# Running and updating the system

Written for the person who keeps this running — the ICT Coordinator, not
a developer. Every procedure assumes the app is live and teachers are
using it, because from Term 1 onwards it is.

> **Always use the project's own Python**, `.venv\Scripts\python.exe`,
> never a bare `python`. The project's packages are installed in that
> virtual environment; the system Python has none of them and fails with
> `ModuleNotFoundError: No module named 'sqlalchemy'`. The form below
> works from both Command Prompt and PowerShell, run from the project
> folder.

---

## 1. The shape of a deployment

Three separate things, and it helps to keep them straight:

| Piece | Where it lives | Who restores it |
|---|---|---|
| **The app** (this code) | The host, deployed from GitHub | A redeploy from git |
| **The data** | Supabase Postgres, Tokyo | Supabase's own backups |
| **The logins** | Supabase **Auth** — a different schema | Supabase's own backups |

The Backup page inside the app downloads **the data only**. It does not
contain logins, which is why it is not a complete disaster-recovery
file. Both matter; neither replaces the other.

Since LibreOffice was removed, the app needs **no OS-level packages at
all** — `requirements.txt` is the whole dependency list. That is what
makes updating cheap.

---

## 2. Updating the app

The routine case — a fix, a wording change, a new page:

```bash
git push origin master
```

**A push does NOT deploy on its own.** It syncs the files to the host, and
the running app keeps serving the old code until the process is restarted —
because `fileWatcherType` is off (see `.streamlit/config.toml`; the watcher
crashes the app by re-importing model modules). Nothing in the app changes
until you **Reboot** it from the Streamlit Cloud dashboard.

The footer of every page tells you which of the two you are looking at:

```
c19e19c · up 34m · ⚠ 41faa98 is on disk — restart to load it
```

Left of the warning is what is *running*; the warning names what is *on
disk* and waiting. No warning means the two agree and the deploy is live.
That line is the only reliable check — the health endpoint answers `ok`
just as happily while the old code is serving, so a green health check
after a push proves nothing about the push.

**What a restart does to people using it:** everyone is signed out, and
anything typed but not saved is lost. `st.session_state` does not
survive a restart. So:

- Deploy **outside encoding hours** wherever possible.
- Never deploy on a submission deadline day.
- If it must happen during the day, tell advisers first.

### Rolling back

```bash
git revert <commit> && git push origin main
```

Roughly as fast as deploying. This is why the next section matters: a
code rollback is easy, a **schema** rollback is not.

---

## 3. Updating the database schema

Schema changes run from your machine, not the host — the host has no
shell. That is deliberate: it keeps schema changes a decision rather
than a side effect of pushing code.

```bash
.venv\Scripts\python.exe -m alembic upgrade head
```

**Order matters, and getting it backwards causes an outage.**

| Change | Order |
|---|---|
| **Additive** — new table, new nullable column | Migrate **first**, then push code |
| **Destructive** — drop or rename a column | Push code that no longer uses it **first**, then migrate |

Never both in one step. The running app must be able to cope with the
database in either state for the moment in between.

**Before any migration: take a backup** from the Backup page. A code
revert is one command; a migration does not undo itself.

---

## 3a. Switching on DO 017's unit system

DepEd Order 017 s. 2026 makes the Term Average and General Average
unit-weighted from SY 2026-2027. It applies to **both grade levels** here:
the order lets non-pilot schools keep Grade 12 on the old curriculum for a
year, but FGNMHS piloted, so that exemption doesn't apply.

It is three separate steps on purpose, and only the third changes a number
anyone sees.

**Step 1 — migrate.** Additive, so it goes before the code push, and on its
own it changes nothing:

```bash
.venv\Scripts\python.exe -m alembic upgrade head
```

**Step 2 — see what would be written.** No flag, no writes:

```bash
.venv\Scripts\python.exe -m scripts.apply_do17_units
```

Read the tail of the output. It lists any subject that would still count as
**1 unit** because DO 017 doesn't settle its weight from its category —
mostly the Field Exposure / Arts cluster, which mixes 80-hour and 160-hour
subjects. Set those on the **Subject Units** page (Setup → Subject Units)
before going on: 80 hours a term is 3, 160 is 6, 320 is 12, and 160 spread
over three terms is 2, and the page has a calculator for anything else. A
wrong unit does not error; it produces a slightly wrong average.

**Step 3 — write, then recompute.** The first command writes the units and
activates the policy. The second rebuilds every learner's cached averages,
and **is the moment the numbers on screen change**:

```bash
.venv\Scripts\python.exe -m scripts.apply_do17_units --confirm
```

```bash
.venv\Scripts\python.exe -m scripts.apply_do17_units --recompute --confirm
```

**Do the recompute outside encoding hours**, and take a backup first. It
touches every non-finalized enrollment. Finalized years are skipped
deliberately — a policy change never rewrites a year that has been closed
out.

**Expect averages to move by a mark or two**, in both directions. That is
the point of the change, not a fault: a 3-unit elective now counts more
than a 2-unit core, where before they counted the same, and a Grade 12
TechPro elective at 12 units now outweighs four academic ones. Tell
advisers before you run it, or the first person to notice will report it as
a bug.

**The Grade 11 language pair changes shape on the term card**, from two
flat rows to a parent row with its two components indented under it — the
way it has always printed on the report card. The parent is the row that
counts; the components are shown so the number can be checked. Worth
mentioning to advisers at the same time.

To check afterwards: the Grade Summary screen shows "Unit-weighted over N
units" beneath a General Average, and the Subject Units page reports
whether every offering resolves to a configured value.

### Changing units later

Use **Setup → Subject Units**. It shows the whole resolution chain —
category defaults, per-subject overrides, combined areas — and what each
subject effectively weighs. Every change is audit-logged.

Two things it will tell you but which are worth knowing in advance:

- **Blank means inherit, and 0 is refused.** There is no way to say
  "don't count this subject"; a 0 would drop it out of the denominator of
  every average without a trace.
- **Editing units does not rebuild existing averages.** They are caches.
  If grades are already encoded, follow an edit with
  `--recompute --confirm` or you get a split: learners recomputed since
  the edit use the new units, everyone else keeps the old ones.

---

## 4. Adding the SF10 template (or any revised DepEd form)

This is expected to happen mid-year, while live. It is a **code update,
not a schema change**, so it is the cheap kind.

1. Put the file in `sf-templates/`, named like the others.
2. Commit and push. The host redeploys.

That is the whole procedure for *replacing* a template whose layout the
renderer already understands.

**Why it is this easy.** `app/xlsx_render.py` reads the geometry the
template itself carries — merges, column widths, row heights, borders,
fonts, images, page setup. It knows nothing about what an SF9 *is*. A
DepEd revision that moves cells around needs no code change at all.

**What still needs code for SF10 specifically:** something has to decide
*which cell gets which value*. The record layer is already built and
frozen (`app/academic_record_service.py`), so that work is a mapping
module in the shape of `app/sf9_report.py` — not a new data model.
Expect it to be a session's work once the file exists, not a phase.

When it lands, label the output **TEMPORARY THREE-TERM SF10 – FOR SCHOOL
USE ONLY** (§36.3).

### Checking a new template before trusting it

```bash
.venv\Scripts\python.exe -m pytest tests/test_xlsx_render.py -q
```

Then generate one form and compare it against the school's own Excel
print of the same file. The five traps that bit SF2 and SF9 — grouped
column widths, optional-digit number formats, one-cell image anchors,
page-count fit flags, and indent-eating word wrap — are all handled, but
a new form can always exercise something none of them did.

---

## 5. Deadlines and grade encoding

These are two separate settings on **School Years & Terms**, and it
matters that they are:

**Submission deadline** — advisory. Once it passes, the Gradebook shows
a teacher a "past the submission deadline" warning saying how many days
late they are. It does **not** stop them encoding. Leave it blank (tick
"No deadline for this term") and no warning ever appears.

**Grade encoding: OPEN / CLOSED** — the actual gate. Only a Super Admin
changes it, and nothing changes it automatically.

So:

- A term can be reopened at any time, past due or not.
- Nothing ever closes a term by the calendar, so nobody gets locked out.
- Reopening encoding is an ordinary setting change, not the audited
  reopen — that one applies to *finalized records*, which is different.

If a teacher says they cannot type a grade, check the OPEN/CLOSED
toggle first. If they say they are being told they are late, that is the
deadline doing its job and nothing is blocked.

---

## 6. "It's still referenced elsewhere" — deleting test data

The app refuses to delete a learner, section or user that anything else
points at. That is deliberate, not a fault: every foreign key is
`ON DELETE RESTRICT`, so a learner's grades, attendance and academic
record cannot be silently destroyed along with them. One test learner
with a few grades and a month of attendance is typically **40-plus rows
across ten tables**.

For real records the answer is never to delete. Use the workflow instead
— log a movement (transferred out, dropped) so the learner's history
stays intact and the forms keep reporting them correctly.

For clearing test data before the real migration, there is a script. It
never runs from the app, and deletes nothing without `--confirm`:

```bash
.venv\Scripts\python.exe -m scripts.purge_test_data --all-learners
```

That is a dry run — it lists what it would remove. Add `--confirm` to
actually delete. Other targets:

```bash
.venv\Scripts\python.exe -m scripts.purge_test_data --learner 107041140016
```

```bash
.venv\Scripts\python.exe -m scripts.purge_test_data --section "STEM - A"
```

```bash
.venv\Scripts\python.exe -m scripts.purge_test_data --user teacher@example.com
```

Order matters and the script enforces it: a section refuses to go while
learners are still enrolled in it, and a user refuses while they still
advise a section. Clear learners first, then sections.

**It refuses outright on a database holding more than 50 learners**, on
the assumption that is real data rather than test rows. Override with
`--force` only if you are certain.

Deleting a *user* is safer than it looks: everything historical about
them — who submitted a grade, who finalized a month, the audit log — is
`ON DELETE SET NULL`, so the record survives the person leaving (§50).
Only their roles, teaching assignments and adviser slots block it.

**Take a backup first.** This is the one operation in the system with no
undo.

---

## 7. Importing learners and grades

Generate the blank templates:

```bash
.venv\Scripts\python.exe -m scripts.make_import_templates
```

They land in `import-templates\` next to the project — one for learners,
one for term grades. Each has three sheets: **Data** (the only one the
importer reads), **Instructions**, and **Reference**, which lists the
exact section and subject names currently in the system.

**Regenerate after adding sections or subjects**, so the Reference sheet
matches. Section and subject names must match exactly or the row is
rejected.

The templates are built from the importer's own column definitions, so
they cannot drift from what it accepts. The LRN column is pre-formatted
as text: Excel otherwise turns a 12-digit LRN into 1.07041E+11 and
destroys the leading zero.

Import learners **before** enrolling them — the learner import creates
records only; enrolment into a section is a separate step on the
Enrollment page.

---

## 8. Adding a teacher mid-year

1. **Users & Roles** → Add User. A temporary password is shown **once** —
   copy it before leaving the page.
2. Give them the password in person or by phone, not email.
3. They sign in and change it from the sidebar.
4. Grant the role, then make the **teacher assignment** (Super Admin →
   Teacher Assignments) — a Subject Teacher with no assignment sees an
   empty Gradebook, which looks like a fault but isn't.

---

## 9. When something goes wrong

**"I can't log in."** Sessions end after 60 minutes idle, and refreshing
the browser signs you out. Sign in again. If the password is genuinely
lost, a Super Admin resets it from Users & Roles.

**"The page says I'm not authorized."** They have no role at all — every
role that exists now has screens. Grant one on Users & Roles.

**"It won't let me past 'Choose your own password'."** That is working as
intended: an account stays there until its holder replaces the temporary
password an admin issued and read. There is no way round it and no need
for one — setting a password takes a moment. Users & Roles shows a 🔑
against every account still in that state.

**"The app is slow or erroring for everyone."** Check Supabase first —
the free tier pauses a project after about a week of inactivity, which
**school holidays can trigger**. Unpausing is a click in the Supabase
dashboard.

**"A grade is wrong and the record is finalized."** Super Admin →
Grade Summary → the learner → Reopen, with a reason. That reverts the
affected grades to DRAFT for re-submission and is recorded in the Audit
Log.

---

## 10. Before each term closes

1. Advisers finalize every attendance month (**Attendance** page).
2. Teachers submit all grades; check the **Dashboard** for terms still
   showing rows without a grade.
3. Registrar recomputes and finalizes each learner (**Grade Summary**).
4. Generate SF9s — the whole section at once from the SF9 page.
5. Take a **backup** and store it somewhere encrypted.
6. Close encoding for the term.
