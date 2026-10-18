"""The text of the in-app Quick Guide — content only.

Kept apart from `app/admin_pages/help.py` so that adding a rule is a
change to data, reviewable on its own, and so the guide can be tested
without a Streamlit runtime. **Imports nothing**, which also keeps it
safe to import from anywhere (see CLAUDE.md, "The deployed host runs a
different Python").

Each entry is `(topic, title, body)`:

- **topic** groups entries under a subheading within a role. Reuse an
  existing topic where one fits; a new topic is worth it only when several
  entries share it. `tests/test_help.py` rejects a blank one.
- **title** is the rule, stated as the thing a reader would get wrong.
- **body** is Markdown.

Deliberately not a feature tour: every entry is a place where the natural
thing produces a wrong record rather than an error. Anything the screen
already makes obvious is left out.
"""

# The three that apply to everyone, and cause the most damage when
# misunderstood — each one is a rule where "the obvious thing" is wrong.
UNIVERSAL = [
    (
        "Basics",
        "The sidebar is grouped by what you're doing",
        "**Overview** and **Grades & Attendance** are the day-to-day work. "
        "**Forms & Reports** is where every printable document lives. "
        "**Setup** is in dependency order — you cannot make a Section without "
        "an Academic Structure, or a Section Subject Offering without a "
        "Section — so when setting up a new school year, work straight down "
        "that list.\n\n"
        "You only see the pages your role allows, so your sidebar will be "
        "shorter than someone else's.",
    ),
    (
        "Basics",
        "A blank is not a zero",
        "Anywhere a grade is blank, or an attendance day hasn't been saved, it "
        "means **nobody has encoded it yet** — not that the learner scored "
        "nothing. Never type 0 to "
        "mean 'not yet'. A 0 is a real grade and it will pull down averages, "
        "print on a report card, and cost a learner an award.",
    ),
    (
        "Basics",
        "You are signed out after 60 minutes idle",
        "Unsaved typing is lost, so press **Save** (on the Gradebook, **Save & "
        "submit grades**) before stepping away. "
        "Refreshing the browser or opening the app in a new tab also signs you "
        "out, so sign in again when that happens.",
    ),
    (
        "Basics",
        "Sensitive changes are recorded",
        "Changing a grade, altering attendance, reopening a finalized record, "
        "overriding an award, assigning a teacher to a subject or moving a "
        "learner is logged with your name, the old value, the new value and "
        "the time. That's normal and expected — it protects you as much as "
        "the record.",
    ),
]

BY_ROLE = {
    "SUBJECT_TEACHER": (
        "Subject Teachers — Gradebook",
        [
            (
                "Grades & Gradebook",
                "You type one final grade per learner, per subject, per term",
                "The system does **not** compute from Written Work / Performance "
                "Task / Exam. Work those out in your own class record as you "
                "always have, then encode the single official term grade here.",
            ),
            (
                "Grades & Gradebook",
                "One button saves and submits",
                "**Save & submit grades** keeps your work and submits it in one "
                "step. Nothing you type is kept until you press it, so press it "
                "often — boxes you leave blank stay blank, and you can finish "
                "the class later. Your adviser sees the grades on Grade Summary "
                "straight away; no one else needs to approve them.",
            ),
            (
                "Grades & Gradebook",
                "You can still edit a submitted grade",
                "Change the number and press **Save & submit grades** again.",
            ),
            (
                "Grades & Gradebook",
                "A red note means some grades aren't submitted yet",
                "Grades brought in from an Excel import, or reopened by a Super "
                "Admin, arrive unsubmitted. Check them, then press **Save & "
                "submit grades**.",
            ),
            (
                "Grades & Gradebook",
                "Encoding closes after the submission deadline",
                "If a term has a deadline, the Gradebook shows it, and you can "
                "encode up to and including that date. From the next day the "
                "class is read-only.",
            ),
            (
                "Grades & Gradebook",
                "If you can't type anything, encoding is closed",
                "Either the term's deadline has passed or an admin has "
                "closed encoding for that term; the Gradebook says which. A "
                "closed term is read-only for everyone.",
            ),
            (
                "Grades & Gradebook",
                "You only see your own assigned classes",
                "If a class is missing, nobody has assigned you to it yet. Ask "
                "**that section's adviser** — they can do it themselves on the "
                "Teacher Assignments page — or an admin.",
            ),
        ],
    ),
    "ADVISER": (
        "Advisers — Your section's teachers, attendance, cards and awards",
        [
            (
                "Enrollment & movements",
                "A learner was enrolled in the wrong section",
                "Open **Enrollment → Section Roster / Movements**, pick the section "
                "they were wrongly put in (one of yours), open the learner's panel and use "
                "**Correct section**. Choose the right section (it must also be one you advise), type a reason, "
                "and press **Move to this section**. It is recorded in the audit "
                "log.\n\n"
                "Don't log a movement for this — *Shifted Out/In* and *Transferred* "
                "are real events and would count on SF2 and SF4.\n\n"
                "Attendance moves with the learner. It is refused once the learner "
                "has **grades or subject substitutions**, or when a month of their "
                "attendance is **finalized** in either section. The message says "
                "what is in the way. A finalized month you can reopen yourself on "
                "Attendance & SF2 (with a reason); for anything else, ask the "
                "registrar, who has to decide what happens to those records.",
            ),
            (
                "Enrollment & movements",
                "The school head says a learner is not to be enrolled in LIS",
                "You can't remove them yourself — only the registrar can "
                "**withdraw an enrollment**. Open the learner's panel on "
                "**Enrollment → Section Roster / Movements** and press **Check "
                "record** to see what they have (attendance, grades, the SF2 "
                "months they appear on), then tell the registrar. Don't log a "
                "*Dropped* or *NLS* movement as a substitute: that keeps the "
                "learner on SF2 and SF4, which is the opposite of what LIS "
                "shows. Once withdrawn, you'll be told which SF2 months to "
                "print again.",
            ),
            (
                "Enrollment & movements",
                "Upload SHS admission records for your class",
                "On the **Learner Masterlist**, open **Update SHS admission "
                "records from a spreadsheet** and download the template. One "
                "row per learner, matched by **LRN** (the only required "
                "column); the other columns are the fields of the learner's "
                "admission form: admission date, JHS/HS completer (Yes or No) "
                "and general average, previous school, PEPT and ALS A&E "
                "passer, rating and exam date, CLC and notes.\n\n"
                "A **blank cell keeps what is stored**, so a partial file is "
                "safe; to clear a value, use the learner's own form. It is "
                "for **Grade 11 learners only**, and only learners you may "
                "edit; any other row is refused with a message and the rest "
                "still go through.",
            ),
            (
                "Enrollment & movements",
                "A learner transfers into your section mid-year",
                "**1. Add the learner.** Search the Learner Masterlist by LRN "
                "first. If a read-only card shows, they are already in the "
                "system under another section or the registrar — don't add "
                "them again (the LRN must be unique); ask the registrar to "
                "enroll them. If nothing shows, add them with the form or the "
                "Excel upload.\n\n"
                "**2. Enroll them** on the Enrollment page into your section.\n\n"
                "**3. Log a *Transferred In* movement** (Enrollment → Section "
                "Roster → the learner's panel → Log movement). Set the "
                "**Effective date** to their first day here, not the day you "
                "encode it — attendance and SF2 depend on it. **Previous "
                "school** is required. Don't just edit the status: the "
                "movement is what stops days before the effective date being "
                "counted, puts them on that month's SF2 with the remark "
                "*Transferred In as of <date> from <school>*, and adds them to "
                "your section's Gradebook rosters.\n\n"
                "If they already passed an elective your section is taking, "
                "use **Subject substitutions** on the same panel.\n\n"
                "**4. Grades from the previous school.** A term whose deadline "
                "has passed is read-only in the Gradebook, so the subject "
                "teachers can't add them. Ask the **registrar** to import "
                "them — you can't. A grade you don't have stays blank, never "
                "0; the learner reads INCOMPLETE on Grade Summary and can't "
                "be finalized until it's filled in.\n\n"
                "Which of the old school's grades counts as our Term 1 (if it "
                "ran semesters or quarters) is for the registrar or principal "
                "to decide — the system won't guess.",
            ),
            (
                "Teaching assignments",
                "You name the teachers for your own section",
                "**Setup → Teacher Assignments**. Pick the school year, pick "
                "your section, and for each subject choose the teacher who "
                "handles it, then press **Assign**.\n\n"
                "You only ever see the sections you advise. If the picker says "
                "you advise none, the school year at the top is probably the "
                "wrong one.",
            ),
            (
                "Teaching assignments",
                "Assigning is what gives that teacher their gradebook",
                "A subject teacher sees a class **only** if they are assigned "
                "to it. So when a colleague says your section isn't showing up "
                "for them, this page is the answer — you don't need to wait for "
                "the admin.\n\n"
                "Because it hands out access, every assignment is recorded with "
                "your name and the date.",
            ),
            (
                "Teaching assignments",
                "One choice covers every term that subject runs",
                "You name a teacher for the *subject*, not for a term. A "
                "three-term core subject takes one Assign; a one-term elective "
                "takes the same one Assign. The caption under each subject "
                "tells you which terms it runs.\n\n"
                "If two different teachers already hold different terms of the "
                "same subject, it says **Split across terms** and names both. "
                "That's allowed — but choosing a name and pressing Assign hands "
                "all of that subject's terms to that one person.",
            ),
            (
                "Teaching assignments",
                "Changing the teacher never touches the grades",
                "Reassigning keeps the previous teacher on record — who taught "
                "what is never lost — and grades already encoded stay exactly "
                "as they are. The new teacher simply picks up the same "
                "gradebook.\n\n"
                "**Unassign** removes access only. Nothing encoded is deleted, "
                "and the subject stays on the learners' report cards.",
            ),
            (
                "Teaching assignments",
                "If a teacher or a subject is missing from that page",
                "A name only appears in the dropdown once the account exists "
                "and carries the **Subject Teacher** role, and a subject only "
                "appears once it is a Section Subject Offering for your "
                "section. Both are admin tasks — ask, rather than working "
                "around it.",
            ),
            (
                "Cards, notices & printing",
                "Parent Notices: who gets the term card, whose parents come in",
                "**Forms & Reports → Parent Notices** sorts your section, per "
                "term, into three groups:\n\n"
                "- **Release**: no failing grade, at most 2 absences, at most 2 "
                "lates and no cutting. Their term card will be emailed to the "
                "parent.\n"
                "- **Concern**: anything more. The parent is asked to come in.\n"
                "- **Not ready**: a grade is still blank or an attendance day "
                "isn't saved (a **P** on the Attendance grid counts only once "
                "you click Save). Finish those and the learner is grouped.\n\n"
                "Attendance doesn't wait: once the days encoded so far reach 3 "
                "absences, 3 lates or 1 cutting, the learner is in Concern and "
                "the parent can be contacted right away, even mid-term.\n\n"
                "Set the **parent meeting** date and time there, and you can "
                "give one learner a different time. If the numbers don't tell "
                "the whole story, **override** a learner into the other group "
                "with a reason.\n\n"
                "Once grade encoding for the term has closed you can **email** "
                "the term cards to Release and a short notice to Concern "
                "(attendance concerns any time). "
                "Concern parents can also be **texted** from your own phone "
                "(press *Mark as texted* after sending) or sent a **printed "
                "letter**. "
                "Parent contacts and consent go on the **Learner Masterlist**.",
            ),
            (
                "Attendance",
                "Encode and print the SF2 on one page: Attendance & SF2",
                "There is no separate SF2 page for advisers, and nothing to "
                "encode anywhere else. On **Grades & Attendance → Attendance & "
                "SF2**:\n\n"
                "1. Mark the absences and lates and press **Save attendance**, "
                "as the month goes.\n"
                "2. After the month's last class day, press **Finalize month**.\n"
                "3. Download the SF2 (Excel or PDF) under **Print SF2**, at the "
                "bottom of the same page.\n\n"
                "The download only appears once the month is finalized, so the "
                "SF2 you print always matches what is saved.\n\n"
                "Most changes don't need a reopen. A class suspension is set "
                "on the school calendar by the admin; a transfer or a drop is a "
                "movement on the Enrollment page; a withdrawal reopens and "
                "finalizes the month by itself. If one of these reaches a "
                "month you already finalized, the page shows a reprint "
                "reminder: download the SF2 again, print it, and press **I've "
                "reprinted the SF2/SF4**. A make-up class added to a finalized "
                "month reopens it instead, so you can mark that day and "
                "finalize again. Reopen a month yourself only to correct a "
                "learner's own mark.",
            ),
            (
                "Attendance",
                "Attendance starts as Present — click Save to record it",
                "Every class day up to today shows **P**, so you only change the "
                "exceptions. Those **P**s are not saved until you click **Save "
                "attendance**, and a month can't be finalized while any are unsaved. "
                "A new learner or a changed calendar just shows up as more unsaved "
                "**P**s; days that haven't happened yet stay blank. To take back a "
                "day you've saved, clear the cell and save — it goes back to not "
                "encoded (a past day then shows its default **P** again).",
            ),
            (
                "Attendance",
                "Late and Cutting still count as present",
                "The learner was in school, so they count as present and are "
                "tallied separately. Use **X** only for a genuine absence.",
            ),
            (
                "Attendance",
                "A learner who leaves mid-month still appears that month",
                "They show on that month's SF2 with a remark and drop off the "
                "next one. That's the DepEd rule, not an oversight.",
            ),
            (
                "Attendance",
                "Finalizing a month locks it",
                "Fix everything shown in red first. After finalizing, you can "
                "still reopen it yourself on Attendance & SF2, with a reason. "
                "If you already handed in that month's SF2, print a new one "
                "once you finalize again. The page reminds you until you press "
                "**I've reprinted the SF2/SF4**.",
            ),
            (
                "Grades & averages",
                "Recompute after grades change, before you print",
                "Grade Summary's figures — Final Grades, the General Average, "
                "whether a record counts as COMPLETE — are stored, not worked "
                "out fresh each time you look. They refresh automatically when "
                "a teacher saves, but if something looks stale after an "
                "unusual change, press **Recompute**. It only re-reads what is "
                "already encoded; it never invents a grade.",
            ),
            (
                "Grades & averages",
                "You don't need to approve teachers' grades",
                "A grade appears on Grade Summary as soon as the subject teacher "
                "presses **Save & submit grades**, and it already counts in the "
                "averages, report cards and awards. There is no separate "
                "verification step.",
            ),
            (
                "Grades & averages",
                "A learner's grades won't finalize until their record is complete",
                "If Finalize is unavailable, at least one applicable subject has "
                "no grade yet. Encode the missing grades, press **Recompute** on "
                "Grade Summary, and the button becomes available.",
            ),
            (
                "Cards, notices & printing",
                "Printing a whole class",
                "The SF9 page has **Print the whole section** — one PDF, one "
                "card per learner. It warns you about anyone whose record isn't "
                "complete: their card prints with blank cells, which a parent "
                "will read as a missing grade.",
            ),
        ],
    ),
    "REGISTRAR": (
        "Registrar — Learners and enrollment",
        [
            (
                "Learners & imports",
                "Upload SHS admission records for many learners",
                "On the **Learner Masterlist**, open **Update SHS admission "
                "records from a spreadsheet** and download the template. One "
                "row per learner, matched by **LRN** (the only required "
                "column); the other columns are the fields of the learner's "
                "admission form: admission date, JHS/HS completer (Yes or No) "
                "and general average, previous school, PEPT and ALS A&E "
                "passer, rating and exam date, CLC and notes.\n\n"
                "A **blank cell keeps what is stored**, so a partial file is "
                "safe; to clear a value, use the learner's own form. It is "
                "for **Grade 11 learners only** — a Grade 12 row is refused "
                "with a message, like every row that has a problem, and the "
                "rest still go through.",
            ),
            (
                "Learners & imports",
                "Upload as Excel (.xlsx)",
                "The LRN is 12 digits and has to arrive whole. From a **.xlsx** "
                "file it always does — you don't need to format the column as "
                "text first.\n\n"
                "**Never save the file as CSV.** Excel shortens a 12-digit LRN "
                "like `107041140016` to `1.07041E+11`, and the last six digits "
                "are gone for good. Those rows are refused rather than guessed "
                "at.\n\n"
                "If your file is a CSV, open it and use **Save As → Excel "
                "Workbook (.xlsx)**, then upload that.",
            ),
            (
                "Learners & imports",
                "Names are saved in capitals",
                "Type them however you like; the system stores and prints them "
                "uppercase so every form matches.",
            ),
            (
                "Enrollment & movements",
                "Log a movement rather than editing the status",
                "Transferring, dropping or shifting a learner should be recorded "
                "as a movement with its effective date. That updates their "
                "status *and* makes the attendance and SF2 rules work — editing "
                "the status alone doesn't.",
            ),
            (
                "Enrollment & movements",
                "A learner transfers in mid-year",
                "**1. Add the learner.** Search the Learner Masterlist by LRN "
                "first. If a card already shows, they are in the system — don't "
                "add them again, the LRN must be unique. If nothing shows, add "
                "them with the form or the Excel upload.\n\n"
                "**2. Enroll them** on the Enrollment page: pick the school "
                "year and section, pick the learner(s), then press **Enroll "
                "selected**.\n\n"
                "**3. Log a *Transferred In* movement** (Enrollment → Section "
                "Roster → the learner's panel → Log movement). Set the "
                "**Effective date** to their first day here, not the day you "
                "encode it — attendance and SF2 depend on it. **Previous "
                "school** is required. Don't just edit the status: the "
                "movement is what stops days before the effective date being "
                "counted, puts them on that month's SF2 with the remark "
                "*Transferred In as of <date> from <school>*, and adds them to "
                "their section's Gradebook rosters.\n\n"
                "If they already passed an elective the section is taking, use "
                "**Subject substitutions** on the same panel to put them in a "
                "different offering for that term.\n\n"
                "**4. Grades from the previous school.** A term whose deadline "
                "has passed is read-only in the Gradebook, so teachers can't "
                "add them. Import them on **Data Import** (Registrar or Super "
                "Admin) — the import doesn't check the deadline and the grades "
                "arrive as DRAFT. The alternative is a Super Admin moving the "
                "deadline, but that reopens the term for *everyone*. A grade "
                "you don't have stays blank, never 0; the learner reads "
                "INCOMPLETE and can't be finalized until it's filled in.\n\n"
                "Which of the old school's grades counts as our Term 1 (if it "
                "ran semesters or quarters) is for the registrar or principal "
                "to decide — the system won't guess.",
            ),
            (
                "Enrollment & movements",
                "A learner was enrolled in the wrong section",
                "Open **Enrollment → Section Roster / Movements**, pick the section "
                "they were wrongly put in, open the learner's panel and use "
                "**Correct section**. Choose the right section, type a reason, "
                "and press **Move to this section**. It is recorded in the audit "
                "log.\n\n"
                "Don't log a movement for this — *Shifted Out/In* and *Transferred* "
                "are real events and would count on SF2 and SF4.\n\n"
                "Attendance moves with the learner, and recomputed averages or "
                "award results are cleared so the next recompute rebuilds them. It "
                "is refused once the learner has **grades, subject substitutions "
                "or an award override**, or when a month of their attendance is "
                "**finalized** in either section — reopen the month first. Grades "
                "are never carried across: remove or reverse them by hand, then "
                "try again.",
            ),
            (
                "Enrollment & movements",
                "Withdrawing an enrollment (a learner who is not to be in LIS)",
                "**Use this only when the school head has directed that a "
                "learner is not enrolled in LIS at all**, so this system should "
                "match it. It is *not* for a learner who left — that is a "
                "*Dropped*, *NLS* or *Transferred Out* movement, which keeps them "
                "on SF2, SF4 and SF9 as DepEd expects. Withdrawing deletes the "
                "learner's year as if it never happened.\n\n"
                "**1. Check the record.** Enrollment → Section Roster / Movements "
                "→ pick the section → open the learner's panel → **Check "
                "record**. It shows the section and adviser, movements, "
                "attendance, grades per term, every kind of data attached, and the "
                "SF2 months the learner appears on. Read the warnings: attendance "
                "marked present after the learner left is leftover data, not an "
                "error on the printed SF2.\n\n"
                "**2. If it says *Can't be withdrawn yet*,** fix what it names "
                "first: reopen finalized grades on Grade Summary, clear award "
                "overrides on Awards. A frozen academic record needs the system "
                "administrator.\n\n"
                "**3. Withdraw.** Under *Withdraw enrollment* pick who **authorized** "
                "it (the school head), type a **reason**, type the learner's "
                "**last name** to confirm, tick the box that lists exactly what "
                "will be deleted, and press **Withdraw enrollment**. Do one "
                "learner at a time and read the result before the next.\n\n"
                "**What happens.** The enrollment, attendance, movements and "
                "grades are deleted; the computed averages are cleared. A full "
                "copy of everything deleted is saved in the audit log. The "
                "learner's own record (name, LRN, birthdate) stays, so they can be "
                "enrolled again later without a duplicate LRN. Any **finalized "
                "attendance month** is reopened and finalized again "
                "automatically, with the original finalizer kept.\n\n"
                "**4. Reprint.** Open the **Withdrawn** tab. *Reprint not yet "
                "confirmed* lists the months whose forms still show the learner — "
                "print those again, replace any copy already submitted, then press "
                "**I've reprinted the SF2/SF4** on the SF2 page and the month "
                "drops off the list. Select an entry to see what was removed, or "
                "download the full copy.\n\n"
                "There is no undo button; the saved copy is how a mistake is put "
                "right, so it can be restored by hand.",
            ),
            (
                "Enrollment & movements",
                "A finalized attendance month can be reopened",
                "You, an admin, or the section's adviser can reopen a finalized "
                "month on the **Attendance & SF2** page, with a reason. Reopened months "
                "are flagged on the **Dashboard** (*Reprint SF2/SF4?* column), on the SF2 "
                "page and on Attendance: an SF2 or SF4 printed before the reopen "
                "may be out of date, so collect a fresh copy once the month is "
                "finalized again, then press **I've reprinted the SF2/SF4** on "
                "either page. That clears the flag; the system can't tell on its "
                "own that you did, so it waits for you.\n\n"
                "Logging or removing a movement whose date reaches into a "
                "finalized month raises the same flag, because that month's "
                "SF2 and SF4 change. If removing one puts a learner back on "
                "days nobody marked, the month is reopened instead, so the "
                "adviser can mark them.",
            ),
            (
                "Learners & imports",
                "Fill in the Section column and the import enrolls them too",
                "On the learner template the **Section** column is optional. "
                "Fill it in and each learner is created *and* placed in that "
                "section in one step. Leave it blank and only the record is "
                "created — you then enroll them on the **Enrollment** page, "
                "which can do a whole batch at once.\n\n"
                "It matters which you did: a learner who exists but isn't "
                "enrolled appears in the masterlist and nowhere else — no "
                "gradebook, no SF2, no report card.",
            ),
            (
                "Learners & imports",
                "You don't need to rename your column headings",
                "`Last Name`, `last_name` and `LASTNAME` are all understood, and "
                "extra columns are ignored — so you can upload a sheet you "
                "already keep without renaming its headings. Dates are read in "
                "whatever order your Excel writes them.\n\n"
                "What still has to be right is the *content*: section and subject "
                "names, and a 12-digit LRN. Anything unrecognised is listed as a "
                "row to fix — nothing is saved until you press the button, and a "
                "bad row never stops the good ones being shown.",
            ),
            (
                "Learners & imports",
                "Section and subject names must match exactly on an import",
                "The import file is checked against what is already in the "
                "system, and a near-miss is rejected rather than guessed at. "
                "The template's **Reference** sheet lists the exact names — "
                "ask the ICT Coordinator to regenerate it after new sections "
                "are created, or it will be listing yesterday's.",
            ),
            (
                "Learners & imports",
                "Imported grades arrive as DRAFT",
                "Nothing imported is treated as final. It still goes through the "
                "normal submit and verify steps. A blank cell in your file stays "
                "blank; it never becomes a 0.",
            ),
            (
                "Learners & imports",
                "Import term grades one section at a time",
                "Pick the school year at the top — section names repeat between "
                "years, and that choice is what keeps a grade off last year's "
                "section of the same name.\n\n"
                "After the grades are saved, every learner in the file has their "
                "averages recalculated. That's the slow part, and it's why a "
                "file per section is better than one file for the whole school. "
                "Let it finish; the progress bar is the recalculation, not the "
                "saving.",
            ),
        ],
    ),
    "SCHOOL_HEAD": (
        "School Head — Read-only view",
        [
            (
                "Reports & oversight",
                "You can see everything",
                "The Dashboard, Grade Summary and the report pages are open to "
                "you school-wide.",
            ),
            (
                "Reports & oversight",
                "Start at the Dashboard",
                "It shows enrolment per section, how many learners have a "
                "complete record, how far grade encoding has got in each term, "
                "and which section-months of attendance are still unfinalized — "
                "which is usually the question worth asking near a deadline. A "
                "month that was **reopened** after finalizing is flagged there "
                "too: an SF2 or SF4 printed before the reopen may be out of date, "
                "so ask the adviser for a fresh copy once it is finalized again. "
                "The flag leaves the list when the adviser or registrar confirms "
                "the reprint.",
            ),
            (
                "Reports & oversight",
                "You can print any report",
                "SF9, SF2 and Term Cards are all available, including the "
                "whole-section SF9 print. Printing changes nothing.",
            ),
        ],
    ),
    "SUPER_ADMIN": (
        "Super Admin — Setup and control",
        [
            (
                "School setup",
                "Section Subject Offerings decides what a learner is graded on",
                "Not the subject catalog, and not the subject profile — those "
                "only seed defaults. If a subject is missing from a gradebook, "
                "or a term is blacked out on a report card, this is the page to "
                "look at.",
            ),
            (
                "School setup",
                "Open the term before teachers can encode",
                "On School Years & Terms. Closing it again makes that term "
                "read-only school-wide, which is the safe state between "
                "encoding periods. A submission deadline, if set, also closes "
                "encoding the day after it — opening the term does not override "
                "that, so to let a teacher finish late, move the deadline or "
                "tick **No deadline**.",
            ),
            (
                "School setup",
                "Generate the calendar, then check November and December",
                "Generation covers weekends and the fixed national holidays, but "
                "it deliberately does not guess at proclamation-dependent days "
                "(Eid, special non-working days, local suspensions). The page "
                "shows a per-month comparison — those two months usually need a "
                "manual adjustment, and changing a day needs a reason.",
            ),
            (
                "School setup",
                "Changing a day in a month already finalized",
                "Every section whose attendance for that month is finalized is "
                "affected, and the page tells you which. **Unticking** a class "
                "day (a suspension) flags each one for an SF2/SF4 reprint; "
                "nothing needs encoding. **Ticking** a new one (a make-up "
                "class) reopens each section with learners who have no mark "
                "that day — the SF2 would otherwise print the blank day as "
                "present — so the advisers mark it and finalize again.",
            ),
            (
                "Corrections & deleting",
                "Reopening a finalized record is possible but audited",
                "It requires a reason and reverts the affected grades to DRAFT "
                "for re-submission. Policy or threshold changes never "
                "recalculate a finished year on their own.",
            ),
            (
                "Corrections & deleting",
                "\"It's still referenced elsewhere\" — how deleting works",
                "Nothing that is in use can be deleted, and that is deliberate: "
                "a learner's grades and attendance would go with them. The "
                "message names what you tried to delete, not what is holding "
                "it, so work down this list:\n\n"
                "- **Strand or track** — held by a Subject Profile or a "
                "Section. Delete the profile first (Subject Profiles → Delete "
                "profile), or leave the strand and just untick **Active**.\n"
                "- **Section** — held by its Section Subject Offerings, "
                "attendance months, and any learners enrolled. Move the "
                "learners out first.\n"
                "- **Subject** — held by any Section Subject Offering that "
                "uses it.\n"
                "- **User** — held by their roles, teaching assignments, and "
                "any section they advise. Clear those three and the delete "
                "works; everything historical about them (who submitted a "
                "grade, who finalized a month) is kept and simply stops "
                "naming them.\n"
                "- **Learner** — see below. Do not delete a real one.",
            ),
            (
                "Corrections & deleting",
                "Untick Active instead of deleting, where you can",
                "For anything with an **Active** checkbox — tracks, strands, "
                "subjects — unticking it is the intended way to retire it. It "
                "disappears from the dropdowns for new records while "
                "everything that already refers to it keeps working. Deleting "
                "is for something created by mistake five minutes ago.\n\n"
                "It also survives a re-seed: the setup script adds back any "
                "missing standard row, so a deleted strand can reappear, while "
                "a deactivated one stays deactivated.",
            ),
            (
                "Corrections & deleting",
                "Never delete a real learner",
                "Use the workflow instead — log a movement (transferred out, "
                "dropped, NLS) on the Enrollment page. Their history stays "
                "intact and the forms report them correctly for the month they "
                "left. Deleting is only for test records, and needs a script "
                "run by the ICT Coordinator.",
            ),
            (
                "School setup",
                "Fill in Subject Profiles before Section Subject Offerings",
                "\"Seed from profile\" on Section Subject Offerings copies "
                "whatever the matching profile contains. If the profile is "
                "empty it copies nothing, silently, and you are left adding "
                "every subject to every section by hand. Set each profile up "
                "once per grade level and track/strand, then seeding a "
                "section is one click.",
            ),
            (
                "School setup",
                "Give every strand a distinct name",
                "Two strands may share a name as far as the database is "
                "concerned — only the code has to be unique. But the forms "
                "print the *name*, so two strands called the same thing are "
                "indistinguishable on SF9 and SF2, and SF4 would report them "
                "as one line to the division. If two specializations are "
                "genuinely different, make their names say so.",
            ),
            (
                "Accounts & backup",
                "Adding the whole staff at once",
                "**Bulk add from Excel**, at the bottom of Users & Roles, takes "
                "an .xlsx of Email, Full Name and Roles — several roles in one "
                "cell, separated by commas. Download the blank template from "
                "that page.\n\n"
                "Each person is asked to choose their own password the first "
                "time they sign in, and can't reach any page until they do — "
                "the one you hand out is one you've read, so it isn't theirs "
                "until they replace it. **Reset password** puts an account back "
                "into that state.\n\n"
                "The temporary passwords appear **once**, as one block you can "
                "copy. Copy them before you leave the page: they are stored "
                "nowhere, and an account whose password was never written down "
                "has to be reset one at a time.\n\n"
                "An address that already has an account is listed and left "
                "alone — uploading the same file twice will not change anyone's "
                "password. Use **Reset password** on the person's own panel "
                "when you actually mean that.",
            ),
            (
                "Accounts & backup",
                "The Backup download is not your only backup",
                "It holds the school's records but **not the login accounts**, "
                "which live in Supabase. Restore from Supabase's own backups "
                "after a real failure; keep this one so the school still holds "
                "its data independently. Treat the file as confidential.",
            ),
        ],
    ),
}

# The short name search and result captions use for each role. Explicit
# rather than cut out of the heading above: the headings are sentences
# ("...teachers, attendance, cards and awards"), and matching a query
# against one would make every entry of the role match "attendance".
ROLE_LABELS = {
    "SUBJECT_TEACHER": "Subject Teachers",
    "ADVISER": "Advisers",
    "REGISTRAR": "Registrar",
    "SCHOOL_HEAD": "School Head",
    "SUPER_ADMIN": "Super Admin",
}
