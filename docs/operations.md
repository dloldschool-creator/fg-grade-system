# Running and updating the system

Written for the person who keeps this running — the ICT Coordinator, not
a developer. Every procedure assumes the app is live and teachers are
using it, because from Term 1 onwards it is.

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
git push origin main
```

The host rebuilds and restarts, usually inside two minutes.

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
./.venv/Scripts/python.exe -m alembic upgrade head
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
./.venv/Scripts/python.exe -m pytest tests/test_xlsx_render.py -q
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

## 6. Adding a teacher mid-year

1. **Users & Roles** → Add User. A temporary password is shown **once** —
   copy it before leaving the page.
2. Give them the password in person or by phone, not email.
3. They sign in and change it from the sidebar.
4. Grant the role, then make the **teacher assignment** (Super Admin →
   Teacher Assignments) — a Subject Teacher with no assignment sees an
   empty Gradebook, which looks like a fault but isn't.

---

## 7. When something goes wrong

**"I can't log in."** Sessions end after 60 minutes idle, and refreshing
the browser signs you out. Sign in again. If the password is genuinely
lost, a Super Admin resets it from Users & Roles.

**"The page says I'm not authorized."** They hold a role with no screens
(currently `ATTENDANCE_ENCODER`), or no role at all.

**"The app is slow or erroring for everyone."** Check Supabase first —
the free tier pauses a project after about a week of inactivity, which
**school holidays can trigger**. Unpausing is a click in the Supabase
dashboard.

**"A grade is wrong and the record is finalized."** Super Admin →
Grade Summary → the learner → Reopen, with a reason. That reverts the
affected grades to DRAFT for re-submission and is recorded in the Audit
Log.

---

## 8. Before each term closes

1. Advisers finalize every attendance month (**Attendance** page).
2. Teachers submit all grades; check the **Dashboard** for terms still
   showing rows without a grade.
3. Registrar recomputes and finalizes each learner (**Grade Summary**).
4. Generate SF9s — the whole section at once from the SF9 page.
5. Take a **backup** and store it somewhere encrypted.
6. Close encoding for the term.
