# Regenerates the two documents in docs/ and the system map they use.
#
# Not part of the application: needs `python-docx` and `Pillow`, neither of
# which is in requirements.txt, and nothing in app/ imports any of this.
# The published .docx files are edited in Word from here on; re-run these
# only to rebuild from scratch or to redraw the map.
#
#     python docs/_generators/build_system_map.py
#     python docs/_generators/build_briefing.py
#     python docs/_generators/build_system_doc.py

"""Draw docs/assets/system-map.png - the one-picture view of the system.

Laid out in points (1pt = 1/72 inch), scaled up by S so the PNG is
print-sharp at the width it is placed in the Word document.
"""
import os

from PIL import Image, ImageDraw, ImageFont

S = 4                      # 288 dpi at final size
PAGE_W = 468               # points -> 6.5 inches, Letter with 1" margins
FONTS = "C:/Windows/Fonts/"

REG, BOLD = "calibri.ttf", "calibrib.ttf"


def font(name, pt):
    return ImageFont.truetype(FONTS + name, int(pt * S))


INK = (28, 33, 40)
NAVY = (11, 62, 122)
NAVY_BG = (232, 240, 249)
TEAL = (14, 100, 102)
TEAL_BG = (228, 243, 242)
AMBER = (140, 86, 12)
AMBER_BG = (253, 244, 227)
GREY = (105, 114, 122)
GREY_BG = (244, 246, 248)
LINE = (168, 180, 192)
WHITE = (255, 255, 255)

H = 520                    # canvas height in points, cropped at the end
img = Image.new("RGB", (PAGE_W * S, H * S), WHITE)
d = ImageDraw.Draw(img)


def px(v):
    return int(round(v * S))


def text(x, y, s, f, fill=INK, anchor="la"):
    d.text((px(x), px(y)), s, font=f, fill=fill, anchor=anchor)


def wrap(s, f, max_pt):
    words, lines, cur = s.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if d.textlength(trial, font=f) <= px(max_pt) or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def box(x, y, w, h, fill, edge, radius=4):
    d.rounded_rectangle(
        [px(x), px(y), px(x + w), px(y + h)],
        radius=px(radius), fill=fill, outline=edge, width=max(1, S // 3),
    )


def chip(x, y, w, h, title, sub, fill, edge, tint):
    box(x, y, w, h, fill, edge)
    f_t = font(BOLD, 7.4)
    f_s = font(REG, 6.4)
    ty = y + 4.5
    for ln in wrap(title, f_t, w - 10):
        text(x + w / 2, ty, ln, f_t, fill=tint, anchor="ma")
        ty += 8.8
    if sub:
        ty += 1.0
        for ln in wrap(sub, f_s, w - 10):
            text(x + w / 2, ty, ln, f_s, fill=GREY, anchor="ma")
            ty += 7.6


def band_label(x, y, s):
    text(x, y, s.upper(), font(BOLD, 6.6), fill=GREY)


def arrow_down(x, y, h, colour=LINE):
    d.line([px(x), px(y), px(x), px(y + h - 3.2)], fill=colour, width=max(1, S // 3))
    d.polygon(
        [(px(x - 2.6), px(y + h - 3.4)), (px(x + 2.6), px(y + h - 3.4)), (px(x), px(y + h))],
        fill=colour,
    )


gap = 6

# --------------------------------------------------------------- title
y = 0
text(0, y, "HOW THE SYSTEM FITS TOGETHER", font(BOLD, 9.5), fill=NAVY)
y += 13
text(0, y, "Read it downwards: people sign in at the top, official forms come out at the bottom.",
     font(REG, 7), fill=GREY)
y += 17

# ------------------------------------------------------- 1. the people
band_label(0, y, "1 - the people who use it")
y += 10
people = [
    ("Subject Teacher", "types one grade per learner, per subject, per term"),
    ("Class Adviser", "attendance, cards, awards; assigns the section's subject teachers"),
    ("Registrar", "learners, enrolment, transfers, bulk imports"),
    ("School Head", "sees everything, prints anything, changes nothing"),
    ("ICT Administrator", "sets up the school year and the rules"),
]
pw = (PAGE_W - gap * 4) / 5
ph = 41
for i, (t, s) in enumerate(people):
    chip(i * (pw + gap), y, pw, ph, t, s, NAVY_BG, NAVY, NAVY)
y += ph

arrow_down(PAGE_W / 2, y + 3, 15, NAVY)
text(PAGE_W / 2 + 7, y + 5.5, "one account each - each person sees only the pages their job needs",
     font(REG, 6.4), fill=GREY)
y += 22

# ---------------------------------------------------------- 2. the app
band_label(0, y, "2 - the web app, opened in any browser, on a phone or a PC")
y += 10
app_h = 40
box(0, y, PAGE_W, app_h, GREY_BG, LINE)
groups = ["Dashboard", "Grades &|Attendance", "Learners", "Forms &|Reports", "Setup", "Administration"]
g_gap = 5
inner = PAGE_W - 12
gw = (inner - g_gap * 5) / 6
f_g = font(BOLD, 6.6)
for i, g in enumerate(groups):
    gx = 6 + i * (gw + g_gap)
    box(gx, y + 6, gw, 20, WHITE, LINE, radius=3)
    lines = g.split("|")
    ly = y + 6 + (20 - len(lines) * 8) / 2
    for ln in lines:
        text(gx + gw / 2, ly, ln, f_g, fill=INK, anchor="ma")
        ly += 8
text(6, y + 29.5, "29 screens grouped by the job in hand - signed out after 60 idle minutes - nothing to install",
     font(REG, 6.2), fill=GREY)
y += app_h

arrow_down(PAGE_W / 2, y + 3, 15, TEAL)
text(PAGE_W / 2 + 7, y + 5.5, "what is typed in is checked, then stored - it is never guessed at",
     font(REG, 6.4), fill=GREY)
y += 22

# -------------------------------------------------------- 3. the rules
band_label(0, y, "3 - the rules that do the arithmetic")
y += 10
rules = [
    ("Grading rules", "unit-weighted averages, DepEd Order 017 s. 2026"),
    ("Attendance rules", "class days taken from the school calendar"),
    ("Award rules", "honours and Academic Excellence thresholds"),
    ("Form builders", "fill the school's own DepEd templates"),
]
rh = 42
rw = (PAGE_W - gap * 3) / 4
for i, (t, s) in enumerate(rules):
    chip(i * (rw + gap), y, rw, rh, t, s, TEAL_BG, TEAL, TEAL)
y += rh + 3
for i, ln in enumerate(wrap(
        "Fixed arithmetic, written down once and tested - the same numbers every time, on every "
        "screen and on every printed form. No AI ever computes a grade.",
        font(REG, 6.2), PAGE_W)):
    text(0, y + i * 7.4, ln, font(REG, 6.2), fill=GREY)
    y_last = y + i * 7.4
y = y_last + 9

arrow_down(PAGE_W * 0.28, y, 14, AMBER)
arrow_down(PAGE_W * 0.80, y, 14, AMBER)
y += 18

# ------------------------------------------------- 4. record + templates
band_label(0, y, "4 - where everything is kept")
y += 10
rec_w = PAGE_W * 0.58
tpl_w = PAGE_W - rec_w - gap
rec_h = 48
box(0, y, rec_w, rec_h, AMBER_BG, AMBER)
text(6, y + 5, "THE RECORD - one online database", font(BOLD, 7.4), fill=AMBER)
for i, ln in enumerate(wrap(
        "46 tables: learners, enrolment, grades, attendance, subjects and their units, "
        "the rules in force for each school year, and an audit trail of every sensitive change.",
        font(REG, 6.4), rec_w - 12)):
    text(6, y + 15.5 + i * 7.6, ln, font(REG, 6.4), fill=INK)
text(6, y + rec_h - 9.5, "Backed up by the host, plus a full copy the school can download at any time.",
     font(REG, 6.0), fill=GREY)

tx = rec_w + gap
box(tx, y, tpl_w, rec_h, AMBER_BG, AMBER)
text(tx + 6, y + 5, "THE OFFICIAL TEMPLATES", font(BOLD, 7.4), fill=AMBER)
for i, ln in enumerate(wrap(
        "The school's own SF9, SF2 and SF4 Excel files - real layout, logos and signature "
        "blocks. The system fills them in; it does not redraw them.",
        font(REG, 6.4), tpl_w - 12)):
    text(tx + 6, y + 15.5 + i * 7.6, ln, font(REG, 6.4), fill=INK)
y += rec_h

arrow_down(PAGE_W / 2, y + 3, 15, NAVY)
y += 22

# ------------------------------------------------------- 5. the outputs
band_label(0, y, "5 - what comes out")
y += 10
outs = [
    ("SF9", "Report Card"),
    ("SF2", "Daily Attendance"),
    ("SF4", "Monthly Movement"),
    ("Term Cards", "one per term"),
    ("Certificates", "honours and awards"),
    ("Exports", "Excel, CSV, backup"),
]
oh = 27
ow = (PAGE_W - gap * 5) / 6
for i, (t, s) in enumerate(outs):
    ox = i * (ow + gap)
    box(ox, y, ow, oh, WHITE, NAVY)
    text(ox + ow / 2, y + 4.5, t, font(BOLD, 7.4), fill=NAVY, anchor="ma")
    for j, ln in enumerate(wrap(s, font(REG, 6.2), ow - 6)):
        text(ox + ow / 2, y + 14.5 + j * 6.6, ln, font(REG, 6.2), fill=GREY, anchor="ma")
y += oh + 13

# --------------------------------------------------------- 6. guarantees
guards = [
    ("A blank is never a zero", "an un-encoded grade stays blank everywhere"),
    ("Every sensitive change is logged", "who, what, before, after, when"),
    ("A finished year is never re-computed", "new rules apply to new years only"),
]
gh = 26
gw2 = (PAGE_W - gap * 2) / 3
for i, (t, s) in enumerate(guards):
    gx = i * (gw2 + gap)
    box(gx, y, gw2, gh, GREY_BG, LINE)
    text(gx + 6, y + 4.5, t, font(BOLD, 6.8), fill=INK)
    text(gx + 6, y + 14, s, font(REG, 6.2), fill=GREY)
y += gh

img = img.crop((0, 0, PAGE_W * S, px(y + 2)))
out = os.path.join(os.path.dirname(__file__), "..", "assets", "system-map.png")
img.save(out, dpi=(72 * S, 72 * S))
print("wrote", out, img.size, "->", round(PAGE_W / 72, 2), "in wide x",
      round(img.size[1] / S / 72, 2), "in tall")
