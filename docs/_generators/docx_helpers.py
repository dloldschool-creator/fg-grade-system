"""Shared python-docx helpers for the two FGNMHS deliverables."""

from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

NAVY = RGBColor(0x0B, 0x3E, 0x7A)
INK = RGBColor(0x1C, 0x21, 0x28)
GREY = RGBColor(0x5F, 0x6A, 0x74)
AMBER = RGBColor(0x8C, 0x56, 0x0C)
TEAL = RGBColor(0x0E, 0x64, 0x66)

HDR_FILL = "0B3E7A"
BAND_FILL = "E8F0F9"
SOFT_FILL = "F4F6F8"
WARN_FILL = "FDF4E3"
RULE = "AAB6C2"

BODY = "Calibri"


# --------------------------------------------------------------- low level
def _el(tag, **attrs):
    e = OxmlElement(tag)
    for k, v in attrs.items():
        e.set(qn("w:" + k), v)
    return e


def shade(cell, fill):
    cell._tc.get_or_add_tcPr().append(_el("w:shd", val="clear", color="auto", fill=fill))


def cell_margins(table, top=40, bottom=40, left=80, right=80):
    tblPr = table._tbl.tblPr
    mar = _el("w:tblCellMar")
    for side, val in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        mar.append(_el("w:" + side, w=str(val), type="dxa"))
    tblPr.append(mar)


def table_borders(table, colour=RULE, size=4, inside=True):
    tblPr = table._tbl.tblPr
    borders = _el("w:tblBorders")
    sides = ["top", "left", "bottom", "right"]
    if inside:
        sides += ["insideH", "insideV"]
    for s in sides:
        borders.append(_el("w:" + s, val="single", sz=str(size), space="0", color=colour))
    tblPr.append(borders)


def no_borders(table):
    tblPr = table._tbl.tblPr
    borders = _el("w:tblBorders")
    for s in ["top", "left", "bottom", "right", "insideH", "insideV"]:
        borders.append(_el("w:" + s, val="none", sz="0", space="0", color="auto"))
    tblPr.append(borders)


def row_header(row):
    """Repeat this row at the top of every page the table spans."""
    trPr = row._tr.get_or_add_trPr()
    trPr.append(_el("w:tblHeader", val="true"))


def no_split_rows(table):
    """Keep each row whole; a row that will not fit moves to the next page."""
    for row in table.rows:
        row._tr.get_or_add_trPr().append(_el("w:cantSplit", val="true"))


def keep_together(paragraph, with_next=True):
    pPr = paragraph._p.get_or_add_pPr()
    if with_next:
        pPr.append(_el("w:keepNext", val="true"))
    pPr.append(_el("w:keepLines", val="true"))


def bottom_rule(paragraph, colour=RULE, size=6, space=2):
    pPr = paragraph._p.get_or_add_pPr()
    bdr = _el("w:pBdr")
    bdr.append(_el("w:bottom", val="single", sz=str(size), space=str(space), color=colour))
    pPr.append(bdr)


def set_page(section, width_in, height_in, margins):
    from docx.shared import Inches
    section.page_width = Inches(width_in)
    section.page_height = Inches(height_in)
    t, r, b, l = margins
    section.top_margin, section.right_margin = Inches(t), Inches(r)
    section.bottom_margin, section.left_margin = Inches(b), Inches(l)


def base_style(doc, size=10.5):
    st = doc.styles["Normal"]
    st.font.name = BODY
    st.font.size = Pt(size)
    st.font.color.rgb = INK
    st.element.rPr.rFonts.set(qn("w:eastAsia"), BODY)
    pf = st.paragraph_format
    pf.space_after = Pt(4)
    pf.space_before = Pt(0)
    pf.line_spacing = 1.05


# --------------------------------------------------------------- text bits
def para(container, text="", size=None, bold=False, italic=False, colour=None,
         align=None, space_before=0, space_after=4, indent=0, spacing=None,
         all_caps=False, font=None):
    p = container.add_paragraph()
    r = p.add_run(text)
    if size:
        r.font.size = Pt(size)
    if font:
        r.font.name = font
    r.bold = bold
    r.italic = italic
    if colour is not None:
        r.font.color.rgb = colour
    if all_caps:
        r.font.all_caps = True
    if align is not None:
        p.alignment = align
    pf = p.paragraph_format
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    if indent:
        from docx.shared import Inches
        pf.left_indent = Inches(indent)
    if spacing:
        pf.line_spacing = spacing
    return p


def rich(container, parts, size=None, align=None, space_before=0, space_after=4,
         indent=0, spacing=None):
    """parts: list of (text, bold, colour) or (text, bold)."""
    p = container.add_paragraph()
    for part in parts:
        text, bold = part[0], part[1]
        colour = part[2] if len(part) > 2 else None
        r = p.add_run(text)
        r.bold = bold
        if size:
            r.font.size = Pt(size)
        if colour is not None:
            r.font.color.rgb = colour
    if align is not None:
        p.alignment = align
    pf = p.paragraph_format
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    if indent:
        from docx.shared import Inches
        pf.left_indent = Inches(indent)
    if spacing:
        pf.line_spacing = spacing
    return p


def bullet(container, text, size=None, indent=0.18, space_after=2, bold_lead=None):
    from docx.shared import Inches
    p = container.add_paragraph()
    r0 = p.add_run("•  ")
    if size:
        r0.font.size = Pt(size)
    r0.font.color.rgb = GREY
    if bold_lead:
        rb = p.add_run(bold_lead)
        rb.bold = True
        if size:
            rb.font.size = Pt(size)
    r = p.add_run(text)
    if size:
        r.font.size = Pt(size)
    pf = p.paragraph_format
    pf.left_indent = Inches(indent + 0.14)
    pf.first_line_indent = Inches(-0.14)
    pf.space_after = Pt(space_after)
    pf.space_before = Pt(0)
    return p


def numbered(container, n, text, size=None, indent=0.0, space_after=2, bold_lead=None):
    from docx.shared import Inches
    p = container.add_paragraph()
    r0 = p.add_run("%d.  " % n)
    r0.bold = True
    r0.font.color.rgb = NAVY
    if size:
        r0.font.size = Pt(size)
    if bold_lead:
        rb = p.add_run(bold_lead)
        rb.bold = True
        if size:
            rb.font.size = Pt(size)
    r = p.add_run(text)
    if size:
        r.font.size = Pt(size)
    pf = p.paragraph_format
    pf.left_indent = Inches(indent + 0.22)
    pf.first_line_indent = Inches(-0.22)
    pf.space_after = Pt(space_after)
    return p


def cell_text(cell, text, size=9, bold=False, colour=None, align=None,
              space_after=1, space_before=1, italic=False):
    cell.text = ""
    p = cell.paragraphs[0]
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.font.name = BODY
    r.bold = bold
    r.italic = italic
    if colour is not None:
        r.font.color.rgb = colour
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.line_spacing = 1.0
    return p


def make_table(doc, rows, cols, widths_in, style="Table Grid"):
    from docx.shared import Inches
    t = doc.add_table(rows=rows, cols=cols)
    t.style = style
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    t.autofit = False
    t._tbl.tblPr.append(_el("w:tblLayout", type="fixed"))
    for r in t.rows:
        for i, c in enumerate(r.cells):
            c.width = Inches(widths_in[i])
    for i, w in enumerate(widths_in):
        t.columns[i].width = Inches(w)
    return t


def data_table(doc, header, rows, widths_in, size=9, head_size=9,
               head_fill=HDR_FILL, zebra=True):
    t = make_table(doc, 1 + len(rows), len(widths_in), widths_in)
    table_borders(t)
    cell_margins(t, top=36, bottom=36, left=70, right=70)
    for i, h in enumerate(header):
        c = t.rows[0].cells[i]
        shade(c, head_fill)
        cell_text(c, h, size=head_size, bold=True, colour=RGBColor(0xFF, 0xFF, 0xFF))
    row_header(t.rows[0])
    no_split_rows(t)
    for j, row in enumerate(rows):
        for i, v in enumerate(row):
            c = t.rows[j + 1].cells[i]
            if zebra and j % 2 == 1:
                shade(c, SOFT_FILL)
            cell_text(c, v, size=size, bold=(i == 0 and len(widths_in) > 1 and False))
    return t


def add_page_number_footer(section, left_text):
    footer = section.footer
    p = footer.paragraphs[0]
    p.text = ""
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.tab_stops.clear_all()
    from docx.enum.text import WD_TAB_ALIGNMENT
    width = section.page_width - section.left_margin - section.right_margin
    p.paragraph_format.tab_stops.add_tab_stop(width, WD_TAB_ALIGNMENT.RIGHT)
    r = p.add_run(left_text + "\t")
    r.font.size = Pt(7.5)
    r.font.color.rgb = GREY
    _field(p, "PAGE")
    r2 = p.add_run(" of ")
    r2.font.size = Pt(7.5)
    r2.font.color.rgb = GREY
    _field(p, "NUMPAGES")
    bottom_rule_top(p)


def bottom_rule_top(paragraph, colour=RULE):
    pPr = paragraph._p.get_or_add_pPr()
    bdr = _el("w:pBdr")
    bdr.append(_el("w:top", val="single", sz="4", space="4", color=colour))
    pPr.append(bdr)


def _field(paragraph, instr):
    r = paragraph.add_run()
    r.font.size = Pt(7.5)
    r.font.color.rgb = GREY
    fld = _el("w:fldChar", fldCharType="begin")
    r._r.append(fld)
    r2 = paragraph.add_run()
    r2.font.size = Pt(7.5)
    r2.font.color.rgb = GREY
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = " %s " % instr
    r2._r.append(it)
    r3 = paragraph.add_run()
    r3.font.size = Pt(7.5)
    r3.font.color.rgb = GREY
    r3._r.append(_el("w:fldChar", fldCharType="end"))


def heading(doc, number, text, size=13, space_before=12, space_after=4):
    p = doc.add_paragraph()
    if number:
        r0 = p.add_run(number + "   ")
        r0.bold = True
        r0.font.size = Pt(size)
        r0.font.color.rgb = NAVY
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(size)
    r.font.color.rgb = NAVY
    pf = p.paragraph_format
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    keep_together(p)
    bottom_rule(p, colour="D5DEE8", size=6, space=2)
    return p


def subheading(doc, text, size=10.5, space_before=8, space_after=2):
    p = para(doc, text, size=size, bold=True, colour=INK,
             space_before=space_before, space_after=space_after)
    keep_together(p)
    return p


def callout(doc, title, body, fill=BAND_FILL, edge=None, size=9.5, title_colour=NAVY):
    t = make_table(doc, 1, 1, [6.5])
    no_borders(t)
    cell_margins(t, top=90, bottom=90, left=120, right=120)
    c = t.rows[0].cells[0]
    shade(c, fill)
    c.text = ""
    p = c.paragraphs[0]
    r = p.add_run(title)
    r.bold = True
    r.font.size = Pt(size)
    r.font.color.rgb = title_colour
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.05
    p2 = c.add_paragraph()
    r2 = p2.add_run(body)
    r2.font.size = Pt(size)
    p2.paragraph_format.space_after = Pt(0)
    p2.paragraph_format.line_spacing = 1.05
    return t


ALIGN_CENTER = WD_ALIGN_PARAGRAPH.CENTER
ALIGN_LEFT = WD_ALIGN_PARAGRAPH.LEFT
ALIGN_JUSTIFY = WD_ALIGN_PARAGRAPH.JUSTIFY
