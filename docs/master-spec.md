# MASTER DEVELOPMENT PROMPT

## FGNMHS Senior High School Three-Term Grading, Attendance, School Forms, and Awards Web Application

Act as a **senior full-stack software architect, database engineer, UI/UX developer, QA engineer, and Philippine DepEd school information systems specialist**.

Build a secure, production-ready, database-driven web application for:

**Francisco G. Nepomuceno Memorial High School – Senior High School**

The application will replace an existing Excel automation workbook used for:

- learner masterlists;
- three-term grading;
- subject profiles;
- subject computations;
- Grade 11 special language computation;
- Grade 11 and Grade 12 electives;
- attendance;
- SF2;
- SF9;
- temporary three-term SF10;
- temporary report cards;
- academic awards;
- award certificates;
- teacher comments;
- printing and PDF generation;
- validation and finalization of school records.

The application must NOT merely recreate an Excel workbook online. It must use a **normalized relational database, role-based security, deterministic calculations, audit logs, report templates, and configurable academic policies**.

---

# 1. PRIMARY SYSTEM OBJECTIVE

Create a school grading and records system for the **DepEd Senior High School three-term curriculum**, beginning with School Year 2026–2027.

The system must support:

1. Grade 11 and Grade 12.
2. Academic and TechPro programs/tracks.
3. Section-specific subject offerings.
4. Term 1, Term 2, and Term 3.
5. Subjects offered:
   - all three terms;
   - only one term;
   - selected terms;
   - different electives in each term.
6. Subject teacher grade encoding.
7. Adviser monitoring and validation.
8. Grade-level and school administrator control.
9. Attendance and learner movement.
10. Automatic computation of final grades and general averages.
11. Special Grade 11 Effective Communication / Mabisang Komunikasyon computation.
12. SF2.
13. SF9.
14. Temporary three-term SF10.
15. Temporary Term Cards.
16. Academic Excellence / award determination.
17. Award Certificates.
18. PDF printing.
19. Excel/CSV import and export.
20. Full audit logs.

All computations that affect official grades must be **deterministic server-side calculations**. Do not use an LLM/AI model to calculate official grades.

---

# 2. APPLICATION ARCHITECTURE

Use a modern secure web architecture.

Recommended production architecture:&#x20;

- Front end: Streamlit — multipage app
- Backend: secure backend
- Database: Supabase (Postgres) — free tier is plenty for a school.
- ORM: Prisma or equivalent
- Authentication: Supabase Auth, role-gated
- File storage: Supabase Storage for generated PDFs
- Deployment: architecture compatible with managed hosting
- Time zone: Asia/Manila

If a different technology stack is selected, retain all functional, security, validation, and database requirements in this specification.

Do not store official school records only in browser localStorage.

---

# 3. MULTI-USER ROLE-BASED ACCESS CONTROL

Implement these roles.

## A. Super Administrator / ICT Administrator

Can:

- configure school information;
- configure academic years;
- configure grading policies;
- create grade levels;
- create tracks/programs;
- maintain subject catalog;
- maintain subject profiles;
- configure electives;
- manage sections;
- create/manage user accounts;
- assign teachers;
- configure school calendar;
- manage reports/templates;
- reopen finalized records;
- create backups;
- restore data where permitted;
- access audit logs;
- export school records.

## B. School Administrator / Registrar

Can:

- view all sections and learners;
- manage enrollment information;
- verify SF9/SF10 data;
- manage historical learner records;
- finalize/reopen records according to permission;
- generate official reports;
- manage learner transfers and status.

## C. Class Adviser

Can:

- manage learners in assigned section;
- review grades submitted by subject teachers;
- encode adviser comments;
- manage attendance;
- manage learner movements;
- validate section records;
- view incomplete grades;
- generate SF2;
- generate SF9;
- generate temporary cards;
- generate certificates;
- submit section records for finalization.

The adviser must NOT silently modify another subject teacher's submitted/finalized grade unless explicitly given administrative authority.

## D. Subject Teacher

Can:

- see only assigned sections and assigned subjects;
- encode grades for the assigned term;
- save drafts;
- submit grades;
- see missing/incomplete entries;
- print/export own class grade sheets.

After submission/finalization, grades are read-only unless the record is formally reopened.

## E. School Head / Read-Only Viewer

Can:

- view dashboards;
- review section summaries;
- review finalized records;
- view/print reports;
- cannot change official data.

---

# 4. SCHOOL SETUP

Create an Administration > School Setup module.

Fields must include:

- School Name
- School ID
- Region
- Schools Division
- District
- School Address
- School Head Name
- School Head Position/Designation
- Current School Year
- Recognition Date
- Recognition Venue
- Default grading policy
- default academic calendar
- adviser information
- other school-form metadata

Do not hardcode these values into PDF templates.

Changes to important school identity values must be recorded in the audit log.

---

# 5. SCHOOL YEAR AND THREE-TERM STRUCTURE

Support multiple school years.

Example:

**SY 2026–2027**

Terms:

- Term 1
- Term 2
- Term 3

Every term must contain:

- start date;
- end date;
- grade encoding status;
- attendance period;
- submission deadline;
- finalization state.

Do not permanently hardcode Term 1/2/3 dates. The administrator must be able to configure the dates for each school year.

Historical school years must remain viewable after a new school year is created.

---

# 6. GRADE LEVELS

Initial supported levels:

- Grade 11
- Grade 12

Design the database so additional levels can theoretically be added later without restructuring the application.

Each section belongs to:

- School Year
- Grade Level
- Track/Program
- Strand/Cluster/Specialization
- Section Name
- Class Adviser

---

# 7. TRACKS / PROGRAMS / STRANDS

Initial profiles from the existing system include:

### Grade 11

- G11-ASSH
- G11-BE
- G11-STEM
- G11-TECHPRO-CP
- G11-TECHPRO-EMS

### Grade 12

- G12-ASSH
- G12-BE
- G12-STEM
- G12-TECHPRO-ICT
- G12-TECHPRO-HE

Program/cluster names used by the existing system include:

- Arts, Social Sciences, and Humanities
- Business and Entrepreneurship
- Science, Technology, Engineering, and Mathematics
- Hospitality and Tourism
- ICT Support and Computer Programming Technologies

Do NOT make these profiles inseparable from application code.

Create administration tables for:

- Grade Level
- Track
- Program/Cluster
- Strand/Specialization
- Subject Profile

Allow new profiles to be added later.

---

# 8. SUBJECT MASTER CATALOG

Every subject record should contain:

- Subject ID
- Subject Code
- Official Subject Name
- Short Name
- Grade Level
- Subject Category
- Learning Area / Parent Area if applicable
- Track/Program restrictions
- active/inactive status
- default grading policy
- sort order
- archival status

Subject names must not be used as the primary database keys.

Use immutable unique IDs.

---

# 9. SUBJECT CATEGORIES AND GRADING COMPONENTS

The current workbook contains the following initial grading configurations.

These must be created as **versioned policy configuration data**, NOT hardcoded formulas.

### Core / Other Academic Elective

- Written Work / Other Written: 20%
- Performance Tasks: 50%
- Term Examination: 30%

### Field Exposure / Arts Apprenticeship / Creative Production & Innovation

- Written/Other: 15%
- Performance: 70%
- Examination: 15%

### Arts / Sports / Health and Wellness

- Written/Other: 20%
- Performance: 60%
- Examination: 20%

### Research / Design and Innovation

- Written/Other: 40%
- Performance: 60%
- Examination: 0%

### TechPro Elective

- Written/Other: 15%
- Performance: 65%
- Examination: 20%

### Work Immersion

- Written/Other: 20%
- Performance: 80%
- Examination: 0%

Validate that every grading policy totals exactly 100%.

The administrator must be able to create a new policy version when DepEd changes the rules.

Never modify the historical policy attached to an already-finalized grade record.

---

# 10. TWO GRADE-ENTRY MODES

Provide two possible methods of entering grades.

## Mode A – Complete Electronic Class Record

Subject teachers enter:

- assessment name;
- maximum score;
- learner raw score;
- assessment category.

The system computes:

1. total raw score;
2. highest possible score;
3. percentage score;
4. weighted score;
5. initial grade;
6. transmuted/official term grade according to the configured policy.

The official transmutation mechanism must be stored as a **versioned policy/table**.

Do NOT invent or approximate a transmutation table.

## Mode B – Official Term Grade Encoding

A subject teacher who already has an official DepEd-compliant e-class record may directly encode/import the official transmuted Term Grade.

Store metadata such as:

- encoded by;
- source;
- date/time;
- import batch;
- grading policy;
- remarks.

The existing Excel workbook primarily operates using this second mode.

---

# 11. TERM SUBJECT ASSIGNMENTS

A subject profile must explicitly state whether each subject is active in:

- Term 1
- Term 2
- Term 3

Use Boolean values or a relational assignment table, not a text code like "111" internally.

For example:

| Subject                                      | T1  | T2  | T3  |
| -------------------------------------------- | --- | --- | --- |
| Effective Communication                      | Yes | Yes | Yes |
| Mabisang Komunikasyon                        | Yes | Yes | Yes |
| Creative Composition 1                       | Yes | No  | No  |
| Creative Composition 2                       | No  | Yes | No  |
| Wika at Komunikasyon sa Akademikong Filipino | No  | No  | Yes |

Only active subjects may receive a grade for that term.

---

# 12. INITIAL GRADE 11 SUBJECT PROFILES

## G11 – ASSH

Subjects offered all three terms:

1. Effective Communication
2. Mabisang Komunikasyon
3. General Mathematics
4. General Science
5. Life and Career Skills
6. Pag-aaral ng Kasaysayan at Lipunang Pilipino

Electives:

### Term 1

- Creative Composition 1

### Term 2

- Creative Composition 2

### Term 3

- Wika at Komunikasyon sa Akademikong Filipino

---

## G11 – Business and Entrepreneurship

Common subjects all three terms:

1. Effective Communication
2. Mabisang Komunikasyon
3. General Mathematics
4. General Science
5. Life and Career Skills
6. Pag-aaral ng Kasaysayan at Lipunang Pilipino

Electives:

### Term 1

- Basic Accounting

### Term 2

- Introduction to Organization and Management

### Term 3

- Business Finance and Income Taxation

---

## G11 – STEM

Common subjects all three terms:

1. Effective Communication
2. Mabisang Komunikasyon
3. General Mathematics
4. General Science
5. Life and Career Skills
6. Pag-aaral ng Kasaysayan at Lipunang Pilipino

Electives:

### Term 1

- Biology 1

### Term 2

- Biology 2

### Term 3

- Chemistry 1

---

## G11 – TechPro Computer Programming

Common subjects all three terms:

- Effective Communication
- Mabisang Komunikasyon
- General Mathematics
- General Science
- Life and Career Skills
- Pag-aaral ng Kasaysayan at Lipunang Pilipino

TechPro subject offered all three terms:

- Computer Programming (.NET Technology) NC III

---

## G11 – TechPro Events Management Services

Common subjects all three terms:

- Effective Communication
- Mabisang Komunikasyon
- General Mathematics
- General Science
- Life and Career Skills
- Pag-aaral ng Kasaysayan at Lipunang Pilipino

TechPro subject offered all three terms:

- Events Management Services NC III

---

# 13. GRADE 12 SUBJECT PROFILES

Grade 12 electives are especially important.

Do NOT assume every Grade 12 section will have identical electives.

Create a **Section Subject Offering** module where the administrator can specify the exact electives for each term.

The existing workbook provides these initial/default offerings.

## G12 – ASSH

### Term 1

- Introduction to Philosophy of the Human Person
- Philippine Politics and Governance
- Contemporary Literature 1
- Filipino sa Isport

Terms 2 and 3 must be configurable according to the school's approved class program.

---

## G12 – Business and Entrepreneurship

### Term 1

- Malikhaing Pagsulat
- Introduction to Philosophy of the Human Person
- Philippine Politics and Governance
- Business Finance and Business Economics

Terms 2 and 3 must be configurable.

---

## G12 – STEM

### Term 1

- Malikhaing Pagsulat
- Pre-Calculus
- Biology 3
- General Physics 1
- General Chemistry 1

Terms 2 and 3 must be configurable.

---

## G12 – TechPro ICT

### Term 1

- Computer Systems Servicing NC II

### Term 2

- Work Immersion

### Term 3

- Contact Center Services

---

## G12 – TechPro HE

### Term 1

- Kitchen Operations

### Term 2

- Tourism Services

### Term 3

- Work Immersion

---

## Generic G12 TechPro

Allow:

### Term 1

- configurable TechPro specialization

### Term 2

- configurable TechPro specialization

### Term 3

- configurable TechPro specialization

Never treat the existing Excel labels "Elective 2" or "Elective 3" as actual subject names.

They are placeholders only.

Before grade encoding is allowed, the administrator should replace every placeholder with the actual approved subject offered to the section.

---

# 14. SPECIAL GRADE 11 LANGUAGE COMPUTATION

This is a critical requirement.

The Grade 11 subjects:

- Effective Communication
- Mabisang Komunikasyon

must be encoded and stored as **two separate subjects**.

Each receives its own grade for Term 1, Term 2, and Term 3.

Example:

| Subject                 | T1 | T2 | T3 |
| ----------------------- | -- | -- | -- |
| Effective Communication | 90 | 98 | 92 |
| Mabisang Komunikasyon   | 92 | 90 | 93 |

Calculate each component final grade independently:

Effective Communication Final:

ROUND(AVERAGE(T1, T2, T3), 0)

Mabisang Komunikasyon Final:

ROUND(AVERAGE(T1, T2, T3), 0)

Using the example:

- Effective Communication Final = 93
- Mabisang Komunikasyon Final = 92

Then calculate the combined learning area's Final Grade:

ROUND(\
AVERAGE(\
Effective Communication Final,\
Mabisang Komunikasyon Final\
),\
0\
)

Result:

**Effective Communication / Mabisang Komunikasyon Final Grade = 93**

---

# 15. COMBINED LANGUAGE TERM DISPLAY

For SF9, also calculate the combined language grade for each term:

Combined Term 1:

ROUND(\
AVERAGE(\
Effective Communication T1,\
Mabisang Komunikasyon T1\
),\
0\
)

Do the same for Terms 2 and 3.

Using the sample above:

- T1 combined = 91
- T2 combined = 94
- T3 combined = 93

---

# 16. GRADE 11 SF9 DISPLAY RULE FOR LANGUAGE SUBJECTS

The SF9 must display:

**Effective Communication / Mabisang Komunikasyon**

followed by indented component rows:

- Effective Communication
- Mabisang Komunikasyon

The component rows show their individual Term 1, Term 2, and Term 3 grades.

However:

**DO NOT print individual Final Grades for the two component rows.**

Their Final Grade cells must remain blank.

Only the parent row:

**Effective Communication / Mabisang Komunikasyon**

shows:

- combined Term 1;
- combined Term 2;
- combined Term 3;
- combined Final Grade;
- Passed/Failed remark.

This rule applies to Grade 11.

---

# 17. TERM AVERAGE COMPUTATION

> **Amended 2026-08-20** to follow DepEd Order No. 017, s. 2026 (Strengthened
> Senior High School Curriculum), Annex E. Two things changed: the Term
> Average is now **weighted by subject units** rather than a flat average, and
> the Grade 11 language pair is counted **once** rather than twice. The
> superseded rule is recorded at the end of this section, because a Term
> Average computed before the amendment was correct under the rule then in
> force and must not be treated as an error.

Within each term, calculate the Term Average using every active subject actually encoded for the learner, weighted by the units each subject carries that term.

Formula:

Term Average =\
ROUND(\
SUM(subject grade × subject units for that term)\
/\
SUM(subject units for that term),\
0\
)

Units come from DO 017 Annex E, Table 19 — see Section 17A.

For Grade 11, Effective Communication and Mabisang Komunikasyon are counted **once**, as the single combined learning area, using the combined term grade defined in Section 15. DO 017 Table 1 establishes the pair as one 160-hour core subject, so it carries **one core subject's units (2)** — not the sum of its two components, which would weight the languages twice.

Example for a G11 academic section, Term 1:

| Entry | Grade | Units |
|---|---|---|
| Effective Communication / Mabisang Komunikasyon (combined) | 85 | 2 |
| General Mathematics | 90 | 2 |
| General Science | 76 | 2 |
| Life and Career Skills | 80 | 2 |
| Pag-aaral ng Kasaysayan at Lipunang Pilipino | 93 | 2 |
| Term-specific elective | 76 | 3 |

Six entries, 13 units. Term Average = ROUND(1076 / 13) = 83.

**Display is not the same question as computation.** The pair is counted once, but it still *prints* the way Section 16 prints it on the report card: the parent row carries the combined grade that counts, with its two component subjects listed beneath it, indented, showing their own term grades. The components are shown so the figure can be checked; they are not added in again. The printed list must always reconcile with the Term Average beneath it.

## Superseded rule (in force before 2026-08-20)

Term Average was the flat average of every active subject grade, and Effective Communication and Mabisang Komunikasyon were counted as **two separate entries** — seven entries for the section above, rather than six. Grades finalized under that rule stand; Section 59's prohibition on retroactive recomputation applies to this amendment as it does to any other.

---

# 17A. SUBJECT UNITS

DO 017, s. 2026, Annex E, Table 19 assigns each subject classification an equivalent number of units **per term**:

| Subject classification | Instructional hours | Units per term |
|---|---|---|
| Core Subjects | 160 across 3 terms | 2 |
| Academic Electives | 80 per term | 3 |
| Arts Electives (Arts Apprenticeship, Creative Production and Presentation) | 160 per term | 6 |
| Tech-Pro Electives, Grade 11 | 320 across 3 terms | 4 |
| Tech-Pro Electives, Grade 12 | 320 per term | 12 |
| Work Immersion | 320 per term | 12 |

Every row is the same rate: **3 units per 80 hours of instruction in a term**. A subject DepEd adds later therefore needs its prescribed hours recorded, not a new rule.

A subject's **annual** units are its units per term multiplied by the number of terms it is actually offered in, taken from Section Subject Offerings (Section 48) — six for a three-term core subject, three for a one-term academic elective. This is what the annual General Average weights by.

The combined Grade 11 language area carries **2** units per term as a single learning area.

**Applicability.** DO 017 phases the Strengthened SHS Curriculum in by grade level — Grade 11 in SY 2026-2027, Grade 12 in SY 2027-2028 — but exempts only learners *not* enrolled in pilot schools. FGNMHS is a pilot school under DepEd Memorandum No. 048, s. 2025, so both grade levels use this system from SY 2026-2027.

**Versioning.** Units and the averaging rules are stored as versioned policy data, not fixed in code, so a later DepEd revision applies to new school years without altering finalized ones (Sections 6 and 59).

---

# 18. SUBJECT FINAL GRADE

For a subject active during all three terms:

Final Grade =\
ROUND(\
AVERAGE(Term1, Term2, Term3),\
0\
)

For a subject offered in only one term:

Final Grade = that term's official grade.

For a subject offered in two terms:

Final Grade =\
ROUND(\
AVERAGE(the two required term grades),\
0\
)

The number of required term grades comes from the subject offering configuration, not from assumptions.

If a required grade is missing:

- display "Incomplete";
- do not silently treat it as zero;
- prevent annual record finalization.

---

# 19. GRADE 11 GENERAL AVERAGE

> **Amended 2026-08-20** to follow DepEd Order No. 017, s. 2026, Annex E.
> The General Average is now **weighted by each subject's annual units**
> rather than a flat average of Final Grades. The rule about counting the
> language pair once is unchanged and was already correct. The superseded
> formula is recorded at the end of this section.

Grade 11 annual General Average has a special rule.

Do NOT count:

- Effective Communication Final
- Mabisang Komunikasyon Final

as two independent final learning areas.

Instead count the combined:

**Effective Communication / Mabisang Komunikasyon Final Grade**

only once, carrying the combined learning area's own annual units (Section 17A) — **not** the sum of its two components' units, which would weight the languages twice and reintroduce exactly the double-counting this section exists to prevent.

Therefore:

General Average =\
ROUND(\
SUM(applicable Final Grade × that subject's annual units)\
/\
SUM(applicable annual units),\
0\
)

where the applicable learning areas are the combined language area plus every other subject the learner was actually offered, each counted once, and annual units are as defined in Section 17A.

## Which Final Grade is weighted

DO 017's worked examples weight the **unweighted, unrounded** subject final — a three-term subject on 76 / 78 / 82 enters the year's total as 78.666…, not as the 79 that prints on the report card. The order's own annex is inconsistent about this: it shows that subject's Final Grade as 78 on one page and 79 on another. The system follows the arithmetic rather than either printed value, because that is what reproduces DO 017's published totals.

The **reported** Final Grade — the whole number on SF9 and SF10 — is unaffected and remains as defined in Section 18. Only the value fed into the weighting differs, and which of the two is used is versioned policy data, not a constant.

## What is NOT weighted

The Grade 11 **lowest final grade** and the **number of failing learning areas** must use the combined language final ONCE, as before, but they are a minimum and a count — units do not enter into either. A 12-unit subject that fails is one failing learning area, not twelve.

## Superseded rule (in force before 2026-08-20)

The General Average was the flat mean of the applicable Final Grades:

ROUND( (Combined Language Final + sum of all other applicable final grades) / (1 + number of other applicable final subjects), 0 )

Grades finalized under that rule stand; Section 59 applies.

---

# 20. GRADE 12 GENERAL AVERAGE

> **Amended 2026-08-20** to follow DepEd Order No. 017, s. 2026, Annex E —
> the same weighting change as Section 19. The superseded formula is
> recorded at the end of this section.

Unless a future policy/profile explicitly defines another combined learning area:

General Average =\
ROUND(\
SUM(applicable Final Grade × that subject's annual units)\
/\
SUM(applicable annual units),\
0\
)

Section 19's rules on which Final Grade is weighted, and on what is not weighted, apply here identically.

Term-specific electives must appear only once in the annual computation, carrying the units of the single term they ran in.

**Weighting matters most in Grade 12.** A Tech-Pro Elective is 12 units per term there — the heaviest weight in Table 19, six times a Core Subject and four times an Academic Elective. DO 017's own Grade 12 Academic-with-cross-track example, eight 3-unit academic electives against one 12-unit Tech-Pro elective, gives **87** as a flat average and **89** weighted. Two marks, on the exact shape this school's Grade 12 Tech-Pro sections have.

**Applicability.** DO 017 would ordinarily keep Grade 12 on the 2016 K to 12 SHS curriculum for SY 2026-2027, but that exemption covers only learners not enrolled in pilot schools. FGNMHS is a pilot school, so this section applies from SY 2026-2027 — see Section 17A.

## Superseded rule (in force before 2026-08-20)

ROUND( AVERAGE(all applicable Grade 12 Final Grades), 0 )

Grades finalized under that rule stand; Section 59 applies.

---

# 21. PASSING RULE

Configure the passing grade in the grading policy.

Initial value:

**75**

Output:

- Final Grade >= 75 → PASSED
- Final Grade < 75 → FAILED

Do not hardcode 75 throughout the application.

Reference a policy setting such as:

passingGrade = 75

---

# 22. TERM COMPLETION CHECK

For every learner and term, calculate:

- Expected Active Subjects
- Encoded Subjects
- Missing Subjects
- Grades Below Passing
- Completion Status

Completion Status:

**COMPLETE**

only when all expected active subjects for that learner's section and term have valid official grades.

Otherwise:

**INCOMPLETE**

Show missing subjects explicitly.

---

# 23. ANNUAL COMPLETION CHECK

Before an annual grade record can be finalized, verify:

- all required terms are finalized;
- all required subject grades exist;
- all final grades can be computed;
- no unknown placeholder subjects remain;
- no invalid grades exist;
- learner enrollment status permits finalization;
- special combined-subject rules are valid.

Do not generate a finalized SF9 or SF10 containing incomplete annual records without displaying a strong warning/watermark.

---

# 24. ACADEMIC AWARD COMPUTATION

Create a versioned **Award Policy** configuration.

The existing workbook's default "Academic Excellence Award" logic is:

Required:

1. Annual record is COMPLETE.
2. Learner has no disqualifying derogatory record.
3. General Average >= 90.
4. Lowest Final Grade >= 80.

If all conditions pass:

**ACADEMIC EXCELLENCE AWARD**

The workbook also retains a legacy configurable Honors mode:

- GA >= 98 → WITH HIGHEST HONORS
- GA >= 95 → WITH HIGH HONORS
- GA >= 90 → WITH HONORS

with no failed subject.

Do not permanently combine these policies.

Create separate award policy versions selectable by School Year.

Show the exact reason when a learner is not eligible, for example:

- General Average below required threshold
- Final Grade below minimum
- Incomplete record
- Derogatory record
- Failed subject

Do not display only "Not Eligible" without an explanation to authorized personnel.

---

# 25. LEARNER MASTERLIST

Each learner enrollment record must support:

### Identity

- Learner Reference Number (LRN)
- Last Name
- First Name
- Middle Name
- Extension Name
- Full Name
- Sex
- Birthdate
- Age

### Enrollment

- School Year
- Grade Level
- Section
- Track
- Strand/Program
- Enrollment Status

### Records

- Derogatory Record? Yes/No
- General Remarks
- Term 1 Adviser Comment
- Term 2 Adviser Comment
- Term 3 Adviser Comment

### Grade 11 / SF10 eligibility/history information

- Date of SHS Admission
- High School Completer? Yes/No
- High School General Average
- High School Completion Date
- Junior High School Completer? Yes/No
- Junior High School General Average
- Previous School Name
- Previous School Address
- PEPT Passer?
- PEPT Rating
- PEPT Examination Date
- ALS A&E Passer?
- ALS A&E Rating
- ALS A&E Examination Date
- CLC Name
- CLC Address
- Other Eligibility / Notes

---

# 26. LRN VALIDATION

LRN must:

- be stored as TEXT rather than numeric;
- preserve leading zeroes;
- contain exactly 12 digits when applicable;
- be unique within active learner records;
- have database validation;
- have duplicate detection during import.

Never expose a learner's LRN in a public URL.

---

# 27. ENROLLMENT STATUS

Support statuses such as:

- Enrolled
- Late Enrollment
- Transferred In
- Transferred Out
- No Longer in School
- Dropped
- Shifted In
- Shifted Out
- Completed
- Graduated
- Other

Keep status history rather than overwriting previous movement information.

Each change requires:

- status;
- effective date;
- explanation/details;
- previous/receiving school when applicable;
- encoded by;
- timestamp.

---

# 28. ATTENDANCE CALENDAR

Create an Academic Calendar module.

Each calendar date should store:

- Date
- Day of Week
- Month
- School Year
- Term
- Default Class Day? Yes/No
- Calendar Override
- Final Class Day? Yes/No
- Note/Reason
- Class-Day Sequence

Initial SY 2026–2027 workbook calendar can be imported as seed data.

Its current monthly instructional-day counts are:

- June – 16
- July – 23
- August – 19
- September – 22
- October – 22
- November – 19
- December – 13
- January – 20
- February – 20
- March – 21
- April – 6

Total in the workbook configuration: **201 class days**.

Treat these values as initial SY 2026–2027 configuration, NOT immutable application code.

Allow administrators to alter dates because of:

- local suspensions;
- holidays;
- make-up classes;
- division/regional instructions;
- national DepEd revisions.

Every calendar override requires:

- reason;
- user;
- timestamp.

---

# 29. SEPTEMBER TERM SPLIT

The existing SY 2026–2027 calendar configuration contains a September transition between Terms 1 and 2.

Do not determine the term only from the month.

Store the **Term ID for each individual calendar date**.

This allows one month to contain dates from different terms.

---

# 30. DAILY ATTENDANCE

Attendance must be stored per:

- learner;
- calendar date;
- attendance status.

Initial attendance codes:

- blank/default → Present
- X → Absent
- T-L → Tardy/Late
- T-C → Cutting Classes

Use internal database status codes rather than relying on text symbols.

Example internal values:

- PRESENT
- ABSENT
- LATE
- CUTTING

The SF2 renderer may convert them to the required printed abbreviations.

---

# 31. ATTENDANCE COMPUTATIONS

Calculate:

- eligible class days;
- days present;
- days absent;
- late count;
- cutting count;
- five consecutive absences warning;
- attendance start date;
- attendance end date;
- active this month;
- active at end of month.

Do not count:

- weekends;
- holidays;
- non-class days;
- dates before a learner becomes active;
- dates after an effective transfer/drop/NLS status;

unless current policy explicitly requires otherwise.

---

# 32. LEARNER MOVEMENT AND SF2

Movement types:

- Late Enrollment
- Transferred In
- Transferred Out
- No Longer in School (NLS)
- Dropped
- Shifted In
- Shifted Out
- Other

Fields:

- Effective Date
- Details
- Previous/Receiving School
- NLS Reason when applicable
- Remarks

Important rule:

A learner who is:

- Transferred Out,
- No Longer in School,
- Dropped,
- Shifted Out

must remain visible in the **effective month's SF2**, with the appropriate remark.

The learner must no longer appear as an active learner in succeeding monthly SF2 reports.

Learners who are:

- Late Enrollees,
- Transferred In,
- Shifted In

begin appearing according to their effective date/month.

---

# 33. MONTHLY ATTENDANCE FINALIZATION

Each month should have a state:

- NOT STARTED
- OPEN
- FOR REVIEW
- FINALIZED

Before finalization:

- identify missing daily attendance;
- identify impossible movement dates;
- verify class-day calendar;
- display male/female enrollment totals;
- display movement summary;
- show five-consecutive-absence warnings;
- check data inconsistencies.

Once finalized, attendance becomes read-only.

An administrator may reopen it only by providing:

- reason;
- authorization;
- date/time.

Log all changes.

---

# 34. SF2 MODULE

Create a School Form 2 report generator.

Allow selection of:

- School Year
- Section
- Month

Automatically populate:

- School Name
- School ID
- District/Division/Region where needed
- Grade Level
- Section
- Track/Program/course information
- Adviser
- Month
- learner names
- sex
- daily attendance marks
- totals
- movement remarks

Separate learners into:

- Male
- Female

The current form layout should provide **25 Male rows and 25 Female rows**.

If enrollment exceeds the printable capacity, implement proper additional pages rather than hiding learners.

Do not require the adviser to select "Page 1/Page 2" simply to see additional learner rows where the system can paginate automatically.

SF2 data must come directly from the attendance database.

Do not duplicate attendance manually in an SF2 table.

Generate:

- preview;
- print;
- PDF download.

---

# 35. SF9 MODULE

Create separate configurable report templates for:

- Grade 11
- Grade 12

The SF9 should automatically populate:

- School information
- School Year
- Learner Name
- LRN
- Age
- Sex
- Grade
- Section
- Track
- Strand/Program
- Adviser where appropriate

## Learning Progress and Achievement

Display columns:

- Learning Area
- Term 1
- Term 2
- Term 3
- Final Grade
- Remarks

Support up to the current template capacity while allowing future template revision.

For Grade 11 implement the special Effective Communication / Mabisang Komunikasyon hierarchy exactly as defined above.

For Grade 12, show the actual section-specific subjects/electives.

Do not print unused placeholder subjects.

Blank unused rows may remain visually blank to preserve the official template.

## Remarks

- PASSED
- FAILED

based on the configured passing grade.

## Attendance

Populate the SF9 attendance portion from finalized attendance data.

Do not require duplicate manual encoding.

## Adviser Comments / Values / Other Fields

Make the report template capable of pulling term adviser comments and other required report fields from the learner record.

---

# 36. SF10 – IMPORTANT TEMPORARY STATUS

The existing workbook uses:

**TEMPORARY THREE-TERM SF10 WORKING COPY – FOR SCHOOL USE ONLY**

This is intentional.

The official permanent three-term SF10 layout may change.

Therefore:

1. Create the underlying permanent learner academic record database independently from the report layout.
2. Treat the current SF10 as a report template only.
3. Clearly label the initial report:

**TEMPORARY THREE-TERM SF10 – FOR SCHOOL USE ONLY**

4. Allow an administrator/developer to replace the report template later WITHOUT migrating/recalculating learner grades.

Never design the database around the visual coordinates of the temporary SF10.

---

# 37. SF10 DATA

SF10 should retrieve:

- School Name
- School ID
- School Year
- Learner Name
- LRN
- Grade/Section
- Track/Program/Strand

For Grade 11, support prior-entry/eligibility fields such as:

- Date of SHS Admission
- Previous School
- Previous School Address
- High School Completer?
- High School General Average
- High School Completion Date
- JHS Completer?
- JHS General Average
- PEPT information
- ALS A&E information
- Community Learning Center information
- other eligibility/notes

Academic records should store/report:

- Subject
- Subject type/category
- Term 1
- Term 2
- Term 3
- Final Grade
- Remarks
- General Average
- Award result where appropriate.

---

# 38. HISTORICAL SF10 DATA IMMUTABILITY

This is extremely important.

When a school year is finalized, create/use historical snapshots for:

- Subject Name
- Subject Code
- Subject Category
- Term applicability
- term grades
- Final Grade
- General Average
- grading policy version
- section
- track/program
- school information

If administrators rename a subject or change a policy in a later school year, historical SF10 records must NOT change.

---

# 39. TEMPORARY TERM CARDS

Create a Temporary Report Card generator.

User selects:

- School Year
- Section
- Term

The report should show only subjects active during the selected term.

Each learner card contains:

- School Name
- Temporary Report Card
- Term
- Learner Name
- LRN
- Grade and Section
- active subjects
- individual term grades
- Term Average
- Adviser
- adviser comment where applicable

Initial print layout:

**6 learners/cards per landscape Letter-size page**

Automatically paginate:

- learners 1–6 → Page 1
- learners 7–12 → Page 2
- etc.

Do not require a user to manually calculate batches.

Provide:

- Print Selected Learner
- Print Section
- Download PDF

---

# 40. AWARD CERTIFICATE MODULE

Create an automatically generated:

**CERTIFICATE OF RECOGNITION**

Populate:

- Learner Name
- Award
- General Average if the template requires it
- School Year
- Recognition Date
- Recognition Venue
- Adviser
- School Head
- School Head designation

Do not hardcode dates.

The current workbook contains an old/example recognition date; the web app must instead pull the configured Recognition Date for the selected school year.

Only learners who satisfy the selected award policy may receive the automated award certificate.

Before printing, display:

**Verify grades, conduct/derogatory record, completion status, and award eligibility.**

Allow administrator overrides only when explicitly permitted, and require an audit-log reason.

---

# 41. REPORT / PRINT CENTER

Create a central **Reports & Printing** page.

Filters:

- School Year
- Grade Level
- Track/Program
- Section
- Learner
- Term
- Month

Reports:

1. SF2
2. SF9
3. Temporary SF10
4. Temporary Term Card
5. Award Certificate
6. Grade Summary
7. Attendance Summary
8. Missing Grades Report
9. Failed Learning Areas Report
10. Award Eligibility Report

Actions:

- Preview
- Download PDF
- Print
- Export where applicable

Record important report generations in a print/report audit log.

---

# 42. DASHBOARD

Create dashboards customized by role.

## Adviser Dashboard

Show:

- Number of learners
- Male/Female count
- enrollment movements
- Term 1 completion %
- Term 2 completion %
- Term 3 completion %
- missing grades
- learners with failing grades
- attendance warnings
- unfinalized months
- award-eligible learners

## Subject Teacher Dashboard

Show:

- assigned subjects
- assigned sections
- current term
- grades entered
- incomplete learners
- submission status
- deadline

## Administrator Dashboard

Show:

- sections
- learners
- teacher assignments
- incomplete gradebooks
- finalized sections
- pending attendance
- movement counts
- report readiness

Do not publicly display identifiable learner information beyond what the signed-in user's role requires.

---

# 43. SECTION GRADEBOOK UI

Create a spreadsheet-like grade entry interface.

Columns:

- No.
- LRN, optionally partially masked
- Learner Name
- applicable subject grade fields
- Term Average
- Number of Grades Below 75
- Completion Status

Features:

- keyboard navigation;
- paste from Excel;
- autosave;
- validation;
- frozen learner-name column;
- sorting;
- filtering;
- warning highlights;
- missing-grade indicator.

Do not save invalid input.

---

# 44. GRADE VALIDATION

Official term grade input must:

- accept only valid numeric grades;
- enforce policy-defined minimum and maximum;
- initially support the expected official grade range;
- reject text accidentally pasted as grades;
- distinguish blank from zero;
- never convert blank values to 0.

A blank grade means:

**NOT YET ENCODED**

It does NOT mean zero.

Use nullable database fields.

---

# 45. GRADE RECORD WORKFLOW

Use states:

1. DRAFT
2. SUBMITTED
3. VERIFIED
4. FINALIZED

### Draft

Teacher can edit.

### Submitted

Teacher indicates completion.

### Verified

Adviser/authorized verifier confirms.

### Finalized

Officially locked.

A finalized grade may only be changed after:

- authorized reopen;
- reason;
- audit record.

Record:

- original value;
- new value;
- changed by;
- timestamp;
- reason.

---

# 46. CONCURRENCY

The system will have multiple teachers using it at the same time.

Prevent one teacher from overwriting another teacher's work.

Implement:

- record-level access control;
- optimistic concurrency/version checking;
- save timestamps;
- conflict handling;
- server-side authorization.

Do not rely only on hiding buttons in the frontend.

---

# 47. SUBJECT TEACHER ASSIGNMENT

Create explicit assignments:

Teacher → Section → Subject → Term

For example:

Teacher A\
→ Grade 12 ICT A\
→ Computer Systems Servicing NC II\
→ Term 1

Only that assigned teacher or authorized administrator may alter the gradebook.

---

# 48. SECTION-SPECIFIC ELECTIVES

This feature is mandatory.

Do not infer electives only from Track.

Create:

**Section Subject Offering**

Fields:

- School Year
- Grade Level
- Section
- Subject
- Term
- Subject Category
- Teacher
- Required/Optional
- Display Order

This permits, for example, two Grade 12 STEM sections to have different approved electives when necessary.

The selected offering controls:

- teacher gradebook;
- Term Average;
- Final Grade;
- General Average;
- SF9;
- SF10;
- temporary cards.

There must be only **one source of truth** for section subject offerings.

---

# 49. DATA MODEL

At minimum create normalized database entities similar to:

### Organization

- schools
- school\_years
- terms

### Users

- users
- roles
- user\_roles
- permissions

### Academic Structure

- grade\_levels
- tracks
- programs
- strands
- sections

### Subjects

- subjects
- subject\_categories
- grading\_policies
- grading\_policy\_versions
- subject\_profiles
- profile\_subjects
- section\_subject\_offerings
- teacher\_assignments

### Learners

- learners
- enrollments
- learner\_status\_history
- learner\_previous\_school\_records
- learner\_eligibility\_records

### Grades

- gradebooks
- term\_grades
- assessment\_categories
- assessments
- learner\_scores
- subject\_final\_grades
- annual\_grade\_summaries
- grade\_finalization\_records

### Attendance

- academic\_calendar\_dates
- attendance\_records
- learner\_movements
- attendance\_month\_status

### Awards

- award\_policies
- award\_policy\_versions
- learner\_awards

### Reports

- report\_templates
- report\_snapshots
- report\_generation\_logs

### Administration

- audit\_logs
- import\_jobs
- export\_jobs
- configuration\_versions

Use foreign keys and proper constraints.

---

# 50. AUDIT LOG

Log every sensitive change.

Examples:

- grade created;
- grade changed;
- grade submitted;
- grade reopened;
- grade finalized;
- attendance altered after initial entry;
- learner transferred;
- learner dropped;
- subject offering changed;
- calendar changed;
- award override;
- user permission changed.

Audit entry should include:

- user;
- action;
- object;
- object ID;
- previous value;
- new value;
- timestamp;
- reason where required;
- IP/device metadata where appropriate.

Normal teachers must not be able to delete audit history.

---

# 51. IMPORT FROM EXISTING EXCEL WORKBOOK

Provide migration utilities.

Allow import of:

- learners;
- subject catalog;
- subject profiles;
- term grades;
- attendance;
- school information.

Use a preview/validation stage:

1. upload;
2. detect columns;
3. map columns;
4. validate;
5. show errors;
6. confirm import;
7. create import audit record.

Never import silently.

Example validation errors:

- duplicate LRN;
- unknown section;
- unknown subject;
- invalid grade;
- impossible date;
- subject not offered during that term.

---

# 52. EXCEL / CSV EXPORT

Authorized users should be able to export:

- section masterlist;
- gradebook;
- final grade summary;
- attendance;
- award eligibility;
- selected administrative reports.

Exports should preserve LRN as text.

---

# 53. SECURITY REQUIREMENTS

Implement:

- secure authentication;
- password hashing;
- role-based authorization;
- server-side permission checks;
- protected report endpoints;
- HTTPS in production;
- CSRF protection where applicable;
- secure cookies/sessions;
- input validation;
- rate limiting;
- safe database queries/ORM;
- backup strategy;
- inactivity/session timeout where appropriate.

Never put service/database administrator secrets into frontend JavaScript.

Never expose all learner records through an unprotected API endpoint.

---

# 54. PRIVACY

Treat learner identity, grades, attendance, LRN, birthdate, and school records as protected information.

Implement least-privilege access.

Examples:

- Subject Teacher sees learners only in assigned classes.
- Adviser sees learners only in assigned sections unless additionally authorized.
- School Head may have read-only access.
- Only administrators may access school-wide records.

Do not make an SF9/SF10 PDF publicly accessible by guessable URLs.

---

# 55. BACKUP AND RECOVERY

Provide an administrative backup process.

At minimum support:

- scheduled database backup;
- downloadable authorized backup/export;
- restore procedures;
- retention policy;
- audit trail.

Before major bulk operations, support creating a recoverable backup/version where technically practical.

---

# 56. REPORT TEMPLATE ENGINE

Separate:

**DATA**

from:

**PRINT TEMPLATE**

This is important because DepEd forms may be revised.

The database should not need restructuring when an SF9 or SF10 template changes.

A report template should map stored fields to printable positions/components.

Version templates by:

- report type;
- School Year;
- effective date;
- revision.

---

# 57. PDF REQUIREMENTS

Reports must print consistently from different computers.

Generate official-looking PDFs on the server rather than relying only on browser print CSS.

Support exact:

- paper size;
- orientation;
- margins;
- page breaks;
- fonts;
- borders;
- row heights;
- signature areas.

Prevent rows from unexpectedly moving between pages.

---

# 58. SYSTEM-WIDE VALIDATION CENTER

Create a **Data Quality / Validation** page.

Identify:

- duplicate LRNs;
- learners without section;
- missing subjects;
- unassigned subject teachers;
- incomplete term grades;
- invalid grade values;
- final grades below passing;
- incomplete attendance;
- movement without effective date;
- invalid calendar configuration;
- placeholder electives;
- award record inconsistent with grades;
- report information missing required school fields.

Use severity:

- ERROR
- WARNING
- INFORMATION

Errors must block finalization where appropriate.

---

# 59. NO SILENT RECALCULATION OF HISTORICAL GRADES

If an administrator changes:

- grading weights;
- passing grade;
- award threshold;
- subject name;
- subject profile;
- combined-subject rule;

previously finalized school years must NOT automatically recalculate.

Use policy versions and snapshots.

A newly created school year may use a newer configuration.

---

# 60. IMPORTANT ROUNDING RULES

Centralize rounding logic.

Do not rely on JavaScript floating-point behavior inconsistently across pages.

Create tested utility functions such as:

- calculateTermAverage()
- calculateSubjectFinalGrade()
- calculateCombinedLanguageTermGrade()
- calculateCombinedLanguageFinalGrade()
- calculateGeneralAverage()
- determinePassFail()
- determineAwardEligibility()

Use consistent DepEd-required rounding rules.

Do not calculate the same official result using separate independent formulas in five different UI components.

---

# 61. GENERAL AVERAGE IS NOT THE AVERAGE OF TERM AVERAGES

This is important.

The annual General Average must be calculated from applicable **Final Grades according to subject structure**, including the Grade 11 combined language rule, and **weighted by each subject's annual units** (Sections 17A, 19, 20).

There are therefore two distinct mistakes to avoid, not one.

**First**, do NOT do:

(Term 1 Average + Term 2 Average + Term 3 Average) / 3

because different electives can be offered during different terms, so the three Term Averages are not built from the same set of subjects and averaging them again is meaningless.

**Second** — added 2026-08-20 — do NOT take the flat mean of the Final Grades either:

AVERAGE(all applicable Final Grades)

because subjects do not carry equal weight. Under DO 017, s. 2026, a Grade 12 Tech-Pro Elective is 12 units and an Academic Elective is 3, so treating them as one entry each understates the subject the learner spent four times as long on. On DepEd's own worked example this is the difference between **87** and **89**.

Both mistakes produce a plausible number rather than an error, which is why this section exists.

---

# 62. SPECIAL RULE ENGINE

Implement special computation rules declaratively where possible.

Initial special rule:

**G11\_COMBINED\_EFFECTIVE\_MABISANG**

Inputs:

- Effective Communication
- Mabisang Komunikasyon

Behavior:

- individual term grades retained;
- individual component finals calculated internally;
- combined term grades created for SF9;
- combined annual final created;
- parent combined learning area counts once toward annual GA;
- individual language component finals are not separately counted in annual GA;
- component final cells are blank on Grade 11 SF9.

This should be configurable enough that another combined learning-area rule could be introduced later without rewriting the whole grading engine.

---

# 63. FINALIZATION PRE-CHECK

When user clicks **Finalize**, display a validation summary.

Example:

Section: Grade 11 ASSH – A

Learners: 42\
Complete: 40\
Incomplete: 2\
Missing Grades: 3\
Attendance Months Pending: 1\
Invalid Elective Placeholders: 0

Finalization must be blocked while critical errors remain.

---

# 64. USER EXPERIENCE

The application must be usable by teachers who are comfortable with Excel but are not programmers.

Requirements:

- clear navigation;
- minimal number of clicks;
- spreadsheet-like grade entry;
- searchable learners;
- descriptive validation messages;
- mobile-responsive monitoring;
- desktop-optimized encoding;
- loading indicators;
- autosave;
- save confirmation;
- breadcrumb/navigation context;
- no cryptic database error messages.

Use terminology teachers recognize:

- School Year
- Grade Level
- Section
- Term
- Learning Area
- Term Grade
- Final Grade
- General Average
- Adviser
- SF2
- SF9
- SF10

---

# 65. IMPORTANT DISPLAY RULE FOR ZERO AND BLANK VALUES

Never display:

0

for a grade/comment/field simply because no value has been entered.

Use:

- blank;
- Not Yet Encoded;
- Not Applicable;

depending on context.

A database NULL is different from numerical zero.

---

# 66. PRINT PREVIEW SAFEGUARDS

Before allowing official report printing, show report readiness:

### Ready

All required data completed.

### Warning

Printable but contains incomplete/non-final data.

### Blocked

Critical information missing.

For temporary/nonfinal reports, automatically add a suitable watermark such as:

**DRAFT**

or:

**TEMPORARY – FOR SCHOOL USE ONLY**

when applicable.

---

# 67. CERTIFICATE SAFEGUARD

Do not create an academic award certificate simply because the General Average reached a threshold.

Check all configured requirements including:

- completion;
- minimum final-grade requirement;
- derogatory/conduct condition;
- selected award policy;
- finalization status.

Allow preview only after eligibility computation.

---

# 68. TESTING REQUIREMENTS

Write automated unit tests for all important calculations.

Required tests include:

### Test A – Grade 11 combined language

Effective Communication:

- 90
- 98
- 92

Final = 93

Mabisang Komunikasyon:

- 92
- 90
- 93

Final = 92

Combined Final:

ROUND((93 + 92) / 2) = 93

Combined term results:

- T1 = 91
- T2 = 94
- T3 = 93

Verify that:

- component final grades exist internally;
- Grade 11 SF9 component final cells remain blank;
- annual General Average counts combined final once.

### Test B – Term-specific elective

Subject active only in Term 2 with grade 91.

Expected:

- T1 = N/A
- T2 = 91
- T3 = N/A
- Final Grade = 91

### Test C – Missing required term

Subject active in T1/T2/T3 but T3 blank.

Expected:

- preview may show current information;
- completion = INCOMPLETE;
- annual finalization blocked.

### Test D – Learner movement

Transferred Out effective September.

Expected:

- appears on September SF2 with remark;
- does not appear as active in October SF2.

### Test E – Award

Record complete;\
GA = 92;\
lowest applicable Final Grade = 84;\
no derogatory record.

Expected under initial Academic Excellence policy:

Eligible.

If lowest Final Grade changes to 79:

Not Eligible.

### Test F – Null handling

Missing grade must remain NULL.

It must never be converted to zero.

### Test G – Unit-weighted averages

> Added 2026-08-20, alongside the Section 17/19/20 amendments.

A unit-weighting error does not raise. It produces a slightly different, entirely plausible number — the wrong mark, on a card that goes home. So each case below must assert both the correct answer **and** the specific wrong answer it replaces.

**G1 – Term Average, Grade 11 academic section, Term 1**

| Entry | Grade | Units |
|---|---|---|
| Effective Communication (encoded) | 80 | — |
| Mabisang Komunikasyon (encoded) | 90 | — |
| → combined language area | 85 | 2 |
| General Mathematics | 90 | 2 |
| General Science | 76 | 2 |
| Life and Career Skills | 80 | 2 |
| Pag-aaral ng Kasaysayan at Lipunang Pilipino | 93 | 2 |
| Term-specific elective | 76 | 3 |

Expected: 13 units, weighted total 1076, **Term Average = 83**.

Must NOT be **84**, which is the pre-amendment answer — the flat mean of seven entries with the two language components counted separately.

**G2 – General Average, Grade 12 academic with cross-track**

Eight Academic Electives at 3 units each (76, 88, 90, 93, 95, 86, 78, 81) and one Tech-Pro Elective at 12 units (96).

Expected: 36 units, weighted total 3213, **General Average = 89**.

Must NOT be **87**, the flat mean of the nine Final Grades. This is DO 017's own worked example and the widest divergence it publishes.

**G3 – The language pair carries one subject's units**

The combined area must weigh **2** units per term, not 4. Counting each component at 2 is the double-count Section 19 exists to prevent, and it survives a casual reading because the resulting average is still in range.

**G4 – Units do not change a subject's own Final Grade**

A three-term subject on 80 / 85 / 90 has a Final Grade of 85 whatever its units. Units weight subjects against each other; they never apply within one subject (Sections 14, 15, 18).

**G5 – Weighting does not defeat null handling**

If any applicable grade is still NULL, the weighted average must be NULL/INCOMPLETE — never a weighted average of the subjects that happen to be encoded, and never with the missing subject's units quietly dropped from the denominator.

**G6 – Table 19 reproduced**

Every classification in Section 17A must return its published unit value, and the whole of DO 017 Annex E's seven worked tables should be reproduced as fixtures. They are published figures rather than values this project chose, which is what makes them worth testing against.

---

# 69. DATABASE TESTING

Create tests for:

- duplicate LRN rejection;
- invalid teacher permissions;
- editing finalized grade rejection;
- subject not active in selected term;
- duplicate subject offering;
- invalid grading policy total;
- invalid attendance date;
- unauthorized report access;
- historical snapshot immutability.

---

# 70. REPORT ACCEPTANCE TESTING

Compare generated reports against the existing workbook/template for:

- SF2
- Grade 11 SF9
- Grade 12 SF9
- Temporary SF10
- Temporary Term Card
- Award Certificate

Check:

- text values;
- calculations;
- spacing;
- paper orientation;
- pagination;
- signatures;
- subject ordering;
- combined language rule;
- attendance totals.

---

# 71. DEVELOPMENT PHASES

Build in this order.

## Phase 1

Architecture, database, authentication, permissions.

## Phase 2

School Year, Grade Level, Track/Program, sections, subject catalog.

## Phase 3

Learners and enrollment.

## Phase 4

Subject profiles and section-specific subject offerings.

## Phase 5

Teacher assignments and term gradebooks.

## Phase 6

Grade computation engine including the Grade 11 combined language rule.

## Phase 7

Annual summary, validation, finalization, and awards.

## Phase 8

Academic calendar and attendance.

## Phase 9

SF2.

## Phase 10

SF9.

## Phase 11

Temporary SF10.

## Phase 12

Temporary Cards and Certificates.

## Phase 13

Excel import/export and migration.

## Phase 14

Audit logs, backups, security hardening.

## Phase 15

Automated tests and production deployment.

Do not attempt to build everything as one huge untested file.

---

# 72. REQUIRED DELIVERABLES

Produce:

1. System architecture.
2. Database ERD/schema.
3. Complete database migrations.
4. Backend/API architecture.
5. Authentication/RBAC.
6. Admin screens.
7. Teacher gradebook.
8. Adviser interface.
9. Attendance interface.
10. grading engine.
11. award engine.
12. report engine.
13. SF2 PDF.
14. SF9 PDF.
15. temporary SF10 PDF.
16. temporary cards PDF.
17. award certificate PDF.
18. Excel/CSV migration tools.
19. automated tests.
20. deployment configuration.
21. administrator documentation.
22. teacher/user guide.
23. backup/restore guide.

---

# 73. IMPORTANT IMPLEMENTATION NOTES

### NOTE 1 — Do not hardcode DepEd policies

Store grading, award, calendar, subject, and report rules as versioned configuration whenever practical.

### NOTE 2 — Existing workbook is the migration baseline

The current FGNMHS Excel automation defines the initial expected calculations and report behavior.

### NOTE 3 — Official policy supersedes workbook behavior

If an official DepEd issuance conflicts with a workbook assumption, flag the conflict and require an administrator-approved policy update rather than silently changing historical records.

### NOTE 4 — Grade 12 electives are configurable

The labels "Elective 2" and "Elective 3" in the workbook are placeholders, not official subject names.

### NOTE 5 — SF10 is temporary

Keep the learner academic database independent of the temporary printable SF10 layout so a future official template can replace it easily.

### NOTE 6 — Effective Communication and Mabisang Komunikasyon

This Grade 11 computation must be unit-tested. It is a major source of possible General Average errors if implemented incorrectly.

### NOTE 7 — Term Average and annual General Average treat the language pair the same way

> **Amended 2026-08-20 alongside Section 17**, and reversed. This note
> previously recorded the two figures as deliberately *different*, which was
> the single most bug-prone rule in the system.

Effective Communication and Mabisang Komunikasyon are counted **once**, as the combined learning area, in both the Term Average (Section 17) and the annual Grade 11 General Average (Section 19). DO 017, s. 2026, Table 1 establishes the pair as one 160-hour core subject, so there is no longer a figure in which the two components count separately.

What still differs between the two figures is the **weight**, not the treatment: the Term Average uses each subject's units for that term, the General Average uses its annual units (Section 17A).

Both components keep their own encoded term grades and their own Final Grades in the database; Section 16 governs which of those a given form prints.

### NOTE 8 — Elective subjects must not be averaged across terms in which they were not offered

A subject offered only in one term has that term grade as its Final Grade.

### NOTE 9 — Never use zero as "blank"

Missing official grades must be NULL/incomplete.

### NOTE 10 — No silent grade changes

Every change after submission/finalization must be attributable to a specific user.

### NOTE 11 — Reports must use the same calculation engine

SF9, SF10, summaries, certificates, and dashboards must not each implement their own independent grading formulas.

### NOTE 12 — Historical records must stay historical

Changes in next year's subject offerings or policy must not alter last year's SF10 or Final Grades.

### NOTE 13 — Attendance and SF2 use one database

Never require attendance to be retyped into the SF2.

### NOTE 14 — Section-specific configuration is essential

Track/profile defaults may initialize a section, but the actual Section Subject Offering must control the learner's gradebook and reports.

### NOTE 15 — Preserve an audit trail

This system handles official learner records. Traceability is more important than making corrections invisible.

---

# 74. ADMIN CONFIGURATION RULE

Wherever possible, follow this hierarchy:

**DepEd Policy Version**\
↓\
**School Year**\
↓\
**Grade Level / Track / Program**\
↓\
**Default Subject Profile**\
↓\
**Section Subject Offering**\
↓\
**Teacher Assignment**\
↓\
**Learner Enrollment**\
↓\
**Term Grade**\
↓\
**Final Grade**\
↓\
**General Average**\
↓\
**School Forms / Awards**

Do not reverse this hierarchy or let PDF reports become the source of truth.

---

# 75. SINGLE SOURCE OF TRUTH

The relational database is the authoritative source.

Examples:

- learner name → learner/enrollment record;
- subject name → subject snapshot/profile;
- term grade → term grade table;
- attendance → daily attendance table;
- final grade → deterministic grading engine;
- award → award engine;
- SF9 → rendered view of stored/calculated records;
- SF10 → rendered historical academic record;
- certificate → rendered award record.

Do not create independent duplicate editable copies of these values inside report modules.

---

# 76. FINAL EXPECTATION

The finished application must allow the school to perform this complete workflow:

1. Administrator creates the School Year.
2. Administrator configures the academic calendar and grading/award policy versions.
3. Administrator creates Grade 11/12 sections.
4. Administrator assigns track/program/strand.
5. A default subject profile is applied.
6. Administrator confirms the actual subjects/electives offered per term.
7. Teachers are assigned subjects and sections.
8. Learners are imported/enrolled.
9. Subject teachers encode Term 1 grades.
10. Subject teachers submit grades.
11. Adviser verifies completion.
12. The same process occurs for Terms 2 and 3.
13. System computes subject Final Grades.
14. For Grade 11, the special Effective Communication / Mabisang Komunikasyon rule is applied.
15. System computes General Average.
16. System checks completion, failed subjects, lowest final grade, and award eligibility.
17. Adviser encodes/finalizes attendance monthly.
18. System automatically prepares SF2.
19. System prepares SF9.
20. System stores/builds the temporary three-term SF10.
21. System generates temporary term cards.
22. Eligible learners may receive Award Certificates.
23. All finalized transactions remain auditable.
24. Historical school years remain available and unchanged.

The result must be a **secure, maintainable, modular school information system**, not a collection of spreadsheet formulas translated directly into web pages.

Before implementing any rule that is ambiguous, compare:

1. the configured policy version;
2. the school's approved subject offering/class program;
3. the migration workbook behavior;
4. the applicable current DepEd issuance.

When there is a discrepancy, show it as a configuration/validation issue rather than silently making assumptions.

---

# 77. SF4 MODULE

Create a School Form 4 report generator (SF4-SHS, Monthly Learners' Movement and Attendance).

Added after the original specification was written. Numbered 77 so that every existing section reference stays valid.

Allow selection of:

- School Year
- Report Month

Changing the report month must regenerate the whole report. No other selection is required.

SF4 is **school-wide**, not per section. One row per Track/Strand actually in use, grouped by grade level:

- Grade 11 rows, followed by TOTAL FOR GRADE 11
- Grade 12 rows, followed by TOTAL FOR GRADE 12
- GRAND TOTAL

Every figure is reported as **Male / Female / Total**.

Automatically populate:

- School Name
- School ID
- District/Division/Region
- School Year
- For the Month of
- Registered Learners as of the end of the month
- Attendance daily average
- Attendance percentage for the month
- Dropped Out, Transferred Out, Transferred In, Shifted Out, Shifted In

Each of the five movement columns is reported three times:

- **(A)** cumulative number as of the previous month;
- **(B)** total for the month;
- **(A+B)** cumulative number as of the end of the month.

## 77.1 Period independence

SF4 reports a **month**. Its figures depend only on dates — who was on the roll on the last class day, which movements were dated inside the month, and attendance across that month's class days.

It therefore does **not** depend on whether the school year is divided into quarters, semesters, or terms, and the official form must **not** be redesigned to accommodate the school's three-term structure.

The form's one period-shaped field, "Semester", is populated with the **term that contains the reporting month**. Where a month spans two terms, the earlier term is named.

## 77.2 Counting rules

These must match the attendance module exactly, so that SF2 and SF4 never disagree about the same month:

- **Registered learners** are those still on the roll on the **last class day** of the month. A learner who transferred out mid-month still appears on that month's SF2 with a remark (§32), but is **not** registered at the end of the month.
- **LATE and CUTTING count as days present** (§30).
- Attendance is measured against each learner's **eligible** class days, never the section's calendar total (§31).
- **Percentages must never be summed across sexes.** The Total percentage is recomputed from the combined present and eligible days. Summing produces impossible figures such as 162%.
- An unused Track/Strand row prints **blank**, not as a row of zeros — a zero is a reported figure, an empty cell is not (§65).

## 77.3 Output

Generate:

- preview;
- Excel (.xlsx) download.

**No PDF is required.** SF4 is submitted as a file rather than printed.

## 77.4 Performance

SF4 aggregates every learner in the school. Its data access must not scale with enrollment: all rows are fetched in batched queries and aggregated in memory, so the query count is a fixed handful regardless of school size.

## 77.5 Related forms not yet specified

SF5-A and SF5-B are **not** covered by this specification. Both are structured around 1st and 2nd semesters, which cannot be filled honestly from a three-term school year. They are deferred pending an updated form from the Schools Division.

Note that SF5-A carries its own guidelines and indicator definitions inside the template (Complete/Incomplete, Regular/Irregular, and the exclusion of learners who are No Longer in School). Those definitions are authoritative when the form is eventually built and must not be re-derived.
