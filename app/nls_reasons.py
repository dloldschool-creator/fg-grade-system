"""Legend 2 ("2. REASONS/CAUSES FOR NLS") transcribed from the official SF2
template (`sf-templates/SF2-template-with-sample-data.xlsx`, rows 78-106) —
the fixed vocabulary the Log Movement form picks an NLS/Dropped reason from,
so the printed remark always names a category DepEd actually recognizes
rather than free text a teacher typed differently every time.

The template's own summary formula (`BU92`) counts "No Longer in School
(NLS)" and "Dropped" into the *same* legend and the same NLS row — that's
why one vocabulary serves both movement types instead of NLS and Dropped
each getting their own list.

"Others" carries no fixed sub-reasons — the template's own row 106 says
"f. Others (Specify)" — so a caller must fall back to free text for it
rather than offering an empty dropdown.

Dependency-free by design, same reasoning as `app/roster_order.py` and
`app/section_access.py` — nothing here should ever affect import order
(see CLAUDE.md's Python-version section).
"""

OTHERS = "Others"

NLS_REASONS: dict[str, list[str]] = {
    "Domestic-Related Factors": [
        "Had to take care of siblings",
        "Early marriage/pregnancy",
        "Parents' attitude toward schooling",
        "Family problems",
    ],
    "Individual-Related Factors": [
        "Illness",
        "Overage",
        "Death",
        "Drug Abuse",
        "Poor academic performance",
        "Lack of interest/Distractions",
        "Hunger/Malnutrition",
    ],
    "School-Related Factors": [
        "Teacher Factor",
        "Physical condition of classroom",
        "Peer influence",
    ],
    "Geographic/Environmental": [
        "Distance between home and school",
        "Armed conflict (incl. Tribal wars & clanfeuds)",
        "Calamities/Disasters",
    ],
    "Financial-Related": [
        "Child labor, work",
    ],
    OTHERS: [],
}
