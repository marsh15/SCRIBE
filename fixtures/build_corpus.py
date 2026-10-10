#!/usr/bin/env python3
"""Build the Scribe 2.0 evaluation corpus (fictional company: Northwind Logistics).

Run:  uv run --no-project --with reportlab --with python-docx --with pillow --with pypdfium2 \
          python fixtures/build_corpus.py

Document text lives in fixtures/corpus_src/*.md (one markdown source per PDF/DOCX/MD/TXT
document). A planted fact is wrapped in the marker pair U+27E6 / U+27E7 inside the source.
For a table row, wrap the whole row line. The markers are stripped on render and the
wrapped text becomes the quote listed in fixtures/corpus/FACTS.md.
CSV documents are generated here (they are data, not prose).
Output is deterministic (fixed seeds, fixed timestamps, invariant PDF mode).
"""
from __future__ import annotations

import csv
import io
import random
import re
import textwrap
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import reportlab.rl_config as rl_config

rl_config.invariant = 1

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "corpus_src"
OUT = ROOT / "corpus"

L, R = "⟦", "⟧"
MARK = re.compile(L + r"(.*?)" + R, re.S)

# ----------------------------------------------------------------------------
# Output manifest: (output file name, source stem or None for generated CSV)
# ----------------------------------------------------------------------------
PDFS = ["hr-leave-policy-2024", "hr-leave-policy-2026", "expense-policy", "security-incident-response",
        "q3-operations-report", "vendor-sla", "onboarding-handbook"]
SCANNED = "facility-safety-notice-scanned"
DOCXS = ["master-services-agreement", "employee-contract-template", "data-processing-addendum",
         "meeting-minutes-2026-03", "travel-guidelines"]
MDS = ["ops-manual", "api-spec", "runbook-deploy", "faq-customers"]
TXTS = ["changelog", "incident-2025-11-postmortem", "glossary"]
CSVS = ["benefits-table", "fleet-inventory", "price-list-2026", "office-locations"]


# ----------------------------------------------------------------------------
# Markdown-ish source parser
# ----------------------------------------------------------------------------
def plain(s: str) -> str:
    return s.replace(L, "").replace(R, "").replace("`", "")


def parse(path: Path):
    """Return dict(header, blocks, facts). blocks: (kind, payload)."""
    raw = path.read_text(encoding="utf-8")
    lines = raw.split("\n")
    header = None
    blocks: list = []
    facts: list[str] = []
    para: list[str] = []
    i = 0

    def flush():
        nonlocal para
        if para:
            text = " ".join(x.strip() for x in para)
            for m in MARK.findall(text):
                facts.append(plain(m))
            blocks.append(("p", text))
            para = []

    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        m = re.match(r"<!--\s*header:\s*(.*?)\s*-->", s)
        if m:
            header = m.group(1)
            i += 1
            continue
        if s == "<!-- pagebreak -->":
            flush()
            blocks.append(("pb", None))
            i += 1
            continue
        if s.startswith("```"):
            flush()
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            for c in code:
                for mm in MARK.findall(c):
                    facts.append(plain(mm))
            blocks.append(("code", code))
            continue
        if not s:
            flush()
            i += 1
            continue
        h = re.match(r"(#{1,4})\s+(.*)", s)
        if h:
            flush()
            blocks.append(("h%d" % len(h.group(1)), plain(h.group(2))))
            i += 1
            continue
        if s.startswith("|") or (s.startswith(L) and s[1:].lstrip().startswith("|")):
            flush()
            rows = []
            while i < len(lines):
                t = lines[i].strip()
                if not (t.startswith("|") or (t.startswith(L) and t[1:].lstrip().startswith("|"))):
                    break
                is_fact = t.startswith(L) and t.endswith(R)
                t = t.replace(L, "").replace(R, "")
                cells = [c.strip() for c in t.strip("|").split("|")]
                i += 1
                if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    continue
                rows.append(cells)
                if is_fact:
                    facts.append(" | ".join(plain(c) for c in cells))
            blocks.append(("table", rows))
            continue
        if s.startswith("- "):
            flush()
            text = s[2:]
            for mm in MARK.findall(text):
                facts.append(plain(mm))
            blocks.append(("li", text))
            i += 1
            continue
        if re.match(r"\d+\.\s", s):
            flush()
            for mm in MARK.findall(s):
                facts.append(plain(mm))
            blocks.append(("ol", s))
            i += 1
            continue
        para.append(ln)
        i += 1
    flush()
    return {"header": header, "blocks": blocks, "facts": facts}


def title_of(doc) -> str:
    for k, p in doc["blocks"]:
        if k == "h1":
            return p
    return "Northwind Logistics"


# ----------------------------------------------------------------------------
# Renderers
# ----------------------------------------------------------------------------
def render_md(path: Path, doc):
    out = []
    for ln in (SRC / (path.stem + ".md")).read_text(encoding="utf-8").split("\n"):
        s = ln.strip()
        if s.startswith("<!-- header:") or s == "<!-- pagebreak -->":
            continue
        out.append(ln.replace(L, "").replace(R, ""))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"
    path.write_text(text, encoding="utf-8")


def table_lines(rows):
    n = max(len(r) for r in rows)
    rows = [r + [""] * (n - len(r)) for r in rows]
    w = [max(len(r[c]) for r in rows) for c in range(n)]
    def fmt(r):
        return "| " + " | ".join(r[c].ljust(w[c]) for c in range(n)) + " |"
    out = [fmt(rows[0]), "|-" + "-|-".join("-" * w[c] for c in range(n)) + "-|"]
    out += [fmt(r) for r in rows[1:]]
    return out


def txt_text(doc, width=78) -> str:
    out = []
    for k, p in doc["blocks"]:
        if k == "h1":
            out += [p, "=" * len(p), ""]
        elif k == "h2":
            out += [p, "-" * len(p), ""]
        elif k in ("h3", "h4"):
            out += [p, ""]
        elif k == "p":
            out += textwrap.wrap(plain(p), width, break_on_hyphens=False, break_long_words=False) + [""]
        elif k == "li":
            out += textwrap.wrap(plain(p), width, break_on_hyphens=False, break_long_words=False, initial_indent="- ", subsequent_indent="  ") + [""]
        elif k == "ol":
            out += textwrap.wrap(plain(p), width, break_on_hyphens=False, break_long_words=False, subsequent_indent="   ") + [""]
        elif k == "code":
            out += ["    " + plain(c) for c in p] + [""]
        elif k == "table":
            out += table_lines([[plain(c) for c in r] for r in p]) + [""]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


def render_pdf(path: Path, doc):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph,
                                    Preformatted, Spacer)

    title = title_of(doc)
    header = doc["header"] or "Northwind Logistics B.V. | " + title
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=10, leading=14, spaceAfter=6)
    st = {
        "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=20, leading=24, spaceAfter=12),
        "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=14, leading=18, spaceBefore=10, spaceAfter=6),
        "h3": ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=11.5, leading=15, spaceBefore=6, spaceAfter=4),
        "h4": ParagraphStyle("h4", fontName="Helvetica-BoldOblique", fontSize=10.5, leading=14, spaceBefore=4, spaceAfter=3),
    }
    li = ParagraphStyle("li", parent=body, leftIndent=16, bulletIndent=4)

    def deco(c, d):
        c.saveState()
        c.setFont("Helvetica", 8)
        c.drawString(20 * mm, A4[1] - 12 * mm, header)
        c.line(20 * mm, A4[1] - 14 * mm, A4[0] - 20 * mm, A4[1] - 14 * mm)
        c.line(20 * mm, 15 * mm, A4[0] - 20 * mm, 15 * mm)
        c.drawString(20 * mm, 10 * mm, "Northwind Logistics B.V. - Confidential - Internal use only")
        c.drawRightString(A4[0] - 20 * mm, 10 * mm, "Page %d" % d.page)
        c.restoreState()

    d = BaseDocTemplate(str(path), pagesize=A4, title=title, author="Northwind Logistics",
                        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm, bottomMargin=20 * mm,
                        invariant=1)
    d.addPageTemplates([PageTemplate(id="p", frames=[Frame(d.leftMargin, d.bottomMargin, d.width, d.height, id="f")],
                                     onPage=deco)])
    fl = []
    for k, p in doc["blocks"]:
        if k in st:
            fl.append(Paragraph(escape(p), st[k]))
        elif k == "p":
            fl.append(Paragraph(escape(plain(p)), body))
        elif k == "li":
            fl.append(Paragraph(escape(plain(p)), li, bulletText="•"))
        elif k == "ol":
            fl.append(Paragraph(escape(plain(p)), li))
        elif k in ("table", "code"):
            ls = table_lines([[plain(c) for c in r] for r in p]) if k == "table" else [plain(c) for c in p]
            mx = max(len(x) for x in ls)
            fs = min(8.5, d.width / (0.6 * mx))
            fl.append(Preformatted("\n".join(ls), ParagraphStyle("m", fontName="Courier", fontSize=fs, leading=fs * 1.3)))
            fl.append(Spacer(1, 8))
        elif k == "pb":
            fl.append(PageBreak())
    d.build(fl)


def render_docx(path: Path, doc):
    import datetime as dt
    from docx import Document
    from docx.shared import Pt

    title = title_of(doc)
    header = doc["header"] or "Northwind Logistics B.V. | " + title
    d = Document()
    cp = d.core_properties
    cp.author = "Northwind Logistics"
    cp.last_modified_by = "Northwind Logistics"
    cp.title = title
    cp.created = cp.modified = dt.datetime(2026, 1, 1, 9, 0, 0)
    cp.revision = 1
    sec = d.sections[0]
    sec.header.paragraphs[0].text = header
    sec.footer.paragraphs[0].text = "Northwind Logistics B.V. - Confidential - Internal use only"
    for k, p in doc["blocks"]:
        if k in ("h1", "h2", "h3", "h4"):
            d.add_heading(p, level=int(k[1]))
        elif k == "p":
            d.add_paragraph(plain(p))
        elif k == "li":
            d.add_paragraph(plain(p), style="List Bullet")
        elif k == "ol":
            d.add_paragraph(plain(p))
        elif k == "code":
            para = d.add_paragraph()
            run = para.add_run("\n".join(plain(c) for c in p))
            run.font.name = "Courier New"
            run.font.size = Pt(9)
        elif k == "table":
            n = max(len(r) for r in p)
            t = d.add_table(rows=len(p), cols=n)
            t.style = "Table Grid"
            for ri, r in enumerate(p):
                for ci in range(n):
                    cell = t.cell(ri, ci)
                    cell.text = plain(r[ci]) if ci < len(r) else ""
                    if ri == 0:
                        for run in cell.paragraphs[0].runs:
                            run.bold = True
            d.add_paragraph("")
    d.save(str(path))
    normalize_zip(path)


def normalize_zip(path: Path):
    """Rewrite a zip with fixed timestamps so output bytes are stable."""
    buf = io.BytesIO()
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in sorted(zin.namelist()):
            zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, zin.read(name))
    path.write_bytes(buf.getvalue())


def render_scanned(pdf_path: Path, truth_path: Path, doc):
    """Image-only PDF: render the text with Pillow, embed the bitmap, no text layer."""
    import reportlab
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    truth = "\n\n".join(textwrap.fill(plain(p), 58, break_on_hyphens=False) for k, p in doc["blocks"] if k == "p") + "\n"
    truth_path.write_text(truth, encoding="utf-8")
    fonts = Path(reportlab.__file__).parent / "fonts"
    font = ImageFont.truetype(str(fonts / "Vera.ttf"), 30)
    W, H = 1240, 1754
    img = Image.new("L", (W, H), 245)
    dr = ImageDraw.Draw(img)
    y = 110
    for ln in truth.split("\n"):
        if ln:
            dr.text((100, y), ln, fill=25, font=font)
        y += 44
    rnd = random.Random(7)
    for _ in range(2500):  # speckle noise
        x, yy = rnd.randrange(W), rnd.randrange(H)
        dr.point((x, yy), fill=rnd.randrange(150, 230))
    img = img.rotate(0.4, resample=Image.BICUBIC, fillcolor=240).filter(ImageFilter.GaussianBlur(0.6))
    c = canvas.Canvas(str(pdf_path), pagesize=A4, invariant=1)
    c.setTitle("Scanned document")
    c.drawImage(ImageReader(img), 0, 0, width=A4[0], height=A4[1])
    c.showPage()
    c.save()


# ----------------------------------------------------------------------------
# CSV documents (generated). Each returns (bytes, [fact row keys])
# ----------------------------------------------------------------------------
def csv_benefits():
    rows = [
        ["benefit_code", "benefit", "eligibility", "employer_pays_pct", "annual_cap_eur", "waiting_period_days"],
        ["BEN-HLT-01", "Health insurance (basic)", "All employees", "85", "", "0"],
        ["BEN-HLT-02", "Health insurance (extended)", "Employees after probation", "60", "1800", "0"],
        ["BEN-DEN-01", "Dental", "Employees after probation", "50", "600", "90"],
        ["BEN-VIS-01", "Vision", "Employees after probation", "50", "300", "90"],
        ["BEN-GYM-01", "Gym membership", "All employees", "40", "360", "30"],
        ["BEN-BIK-01", "Bicycle lease", "Employees in NL and BE", "100", "1500", "180"],
        ["BEN-TRN-01", "Public transport pass", "Employees without a parking permit", "100", "1200", "0"],
        ["BEN-EAP-01", "Employee assistance programme", "All employees and household members", "100", "", "0"],
        ["BEN-LRN-01", "Training budget", "All employees", "100", "1000", "0"],
        ["BEN-LIF-01", "Life insurance", "Permanent employees", "100", "", "60"],
        ["BEN-MEL-01", "Meal vouchers", "Employees in FR and IT", "70", "1080", "0"],
        ["BEN-CHC-01", "Childcare contribution", "Employees with children under 12", "30", "2400", "180"],
    ]
    keys = ["BEN-HLT-01", "BEN-DEN-01", "BEN-BIK-01", "BEN-MEL-01", "BEN-CHC-01", "BEN-LRN-01", "BEN-LIF-01"]
    return rows_to_csv(rows, ","), keys, False


def csv_fleet():
    rnd = random.Random(2026)
    depots = ["D-RTM", "D-HAM", "D-ANR", "D-LYS", "D-MIL", "D-WAW", "D-AUS"]
    plates = {"D-RTM": "NL", "D-HAM": "HH", "D-ANR": "BE", "D-LYS": "FR", "D-MIL": "IT", "D-WAW": "PL", "D-AUS": "TX"}
    types = [("Tractor unit", "NW-T", [("Scania", "R450"), ("Volvo", "FH500"), ("DAF", "XF480")], 18000),
             ("Reefer truck", "NW-R", [("Scania", "P280"), ("Mercedes", "Actros 1845")], 9500),
             ("Box truck", "NW-B", [("MAN", "TGL 12.250"), ("Iveco", "Eurocargo")], 6200),
             ("Van", "NW-V", [("Mercedes", "Sprinter 317"), ("Ford", "Transit 350")], 1300)]
    status_pool = ["in_service"] * 8 + ["maintenance", "reserve"]
    rows = [["vehicle_id", "type", "make", "model", "year", "home_depot", "plate", "status", "last_service", "odometer_km", "payload_kg"]]
    hand = [
        ["NW-T-0417", "Reefer truck", "Scania", "R450", "2022", "D-HAM", "HH-NW 417", "in_service", "2026-08-19", "312408", "9500"],
        ["NW-V-0288", "Van", "Mercedes", "Sprinter 317", "2023", "D-AUS", "TX-4471", "maintenance", "2026-09-30", "88120", "1300"],
        ["NW-T-0555", "Tractor unit", "Volvo", "FH500", "2016", "D-LYS", "FR-555-NW", "retired", "2025-11-04", "1204775", "18000"],
        ["NW-B-0702", "Box truck", "MAN", "TGL 12.250", "2021", "D-MIL", "MI-702-NW", "in_service", "2026-07-02", "201330", "6200"],
        ["NW-R-0119", "Reefer truck", "Mercedes", "Actros 1845", "2024", "D-RTM", "NL-119-NW", "in_service", "2026-09-12", "64210", "9500"],
    ]
    used = {h[0] for h in hand}
    n = 1
    while len(rows) - 1 + len(hand) < 60:
        t, pre, mm, pay = types[rnd.randrange(len(types))]
        vid = "%s-%04d" % (pre, rnd.randrange(100, 900))
        if vid in used:
            continue
        used.add(vid)
        make, model = mm[rnd.randrange(len(mm))]
        dep = depots[rnd.randrange(len(depots))]
        yr = rnd.randrange(2015, 2026)
        rows.append([vid, t, make, model, str(yr), dep, "%s-%03d-NW" % (plates[dep], rnd.randrange(100, 999)),
                     status_pool[rnd.randrange(len(status_pool))], "2026-%02d-%02d" % (rnd.randrange(1, 10), rnd.randrange(1, 28)),
                     str(rnd.randrange(20000, 900000)), str(pay)])
    rows[1:1] = hand[:2]
    rows += hand[2:]
    rows[1:] = sorted(rows[1:], key=lambda r: r[0])
    return rows_to_csv(rows, ","), ["NW-T-0417", "NW-V-0288", "NW-T-0555", "NW-B-0702", "NW-R-0119"], False


def csv_prices():
    rows = [
        ["sku", "description", "unit", "price_eur", "min_order_qty", "valid_from"],
        ["NW-PAL-120", "EUR pallet 120x80 cm standard", "each", "14,90", "50", "2026-01-01"],
        ["NW-PAL-100", "Pallet 100x120 cm lightweight", "each", "12,60", "50", "2026-01-01"],
        ["NW-BOX-S", "Carton small 30x20x20 cm", "pack of 25", "9,40", "20", "2026-01-01"],
        ["NW-BOX-M", "Carton medium 40x30x30 cm", "pack of 25", "11,20", "20", "2026-01-01"],
        ["NW-BOX-L", "Carton large 60x40x40 cm", "pack of 10", "13,80", "10", "2026-01-01"],
        ["NW-COLD-04", "Insulated cold box 4 litre with gel pack", "each", "31,50", "10", "2026-03-01"],
        ["NW-GEL-02", "Cooling gel pack 200 g", "pack of 12", "6,75", "10", "2026-03-01"],
        ["NW-WRAP-18", "Stretch film 18 micron roll", "roll", "7,95", "12", "2026-01-01"],
        ["NW-TAPE-48", "Packing tape 48 mm x 66 m", "pack of 6", "8,10", "10", "2026-01-01"],
        ["NW-LBL-A6", "Thermal label roll A6, 500 labels", "roll", "5,30", "20", "2026-01-01"],
        ["NW-STRAP-PP", "Polypropylene strapping 12 mm x 1000 m", "roll", "24,00", "4", "2026-01-01"],
        ["STO-PAL-WK", "Pallet storage per week", "per pallet", "3,20", "", "2026-01-01"],
        ["FUEL-SUR", "Fuel surcharge", "percent of freight", "6,5", "", "2026-05-01"],
    ]
    keys = ["NW-PAL-120", "NW-BOX-M", "NW-COLD-04", "NW-STRAP-PP", "STO-PAL-WK", "FUEL-SUR"]
    return rows_to_csv(rows, ";", bom=True), keys, True


def csv_offices():
    # No header row; rows are ragged (5 to 8 fields); one address contains a comma inside quotes.
    rows = [
        ["HQ", "Rotterdam", "NL", "Waalhaven Zuidzijde 12, 3089 JH Rotterdam", "Marta Veldkamp", "+31 10 555 0100", "Head office since 2009"],
        ["D-RTM", "Rotterdam", "NL", "Waalhaven Zuidzijde 12, 3089 JH Rotterdam", "Dirk van Leeuwen", "+31 10 555 0110"],
        ["D-HAM", "Hamburg", "DE", "Hansestrasse 41, 20457 Hamburg", "Katrin Brandt", "+49 40 555 0133", "Opened 2012"],
        ["D-ANR", "Antwerp", "BE", "Noorderlaan 88, 2030 Antwerp", "Anke Peeters", "+32 3 555 0177"],
        ["D-LYS", "Lyon", "FR", "Rue des Entrepots 9, 69007 Lyon", "Sophie Marchand", "+33 4 555 0121", "Cross-dock expansion opened 2026-08-14", "6 loading doors"],
        ["D-MIL", "Milan", "IT", "Via Toscana 15, 20090 Milan", "Giulia Ferrara"],
        ["D-WAW", "Warsaw", "PL", "ul. Logistyczna 3, 02-232 Warsaw", "Piotr Nowak", "+48 22 555 0166", "Headcount cap 310"],
        ["D-AUS", "Austin", "US", "2210 Freight Way, Austin, TX 78744", "Mateo Ruiz"],
    ]
    keys = ["D-HAM", "D-LYS", "D-MIL", "D-WAW", "D-AUS", "D-RTM"]
    return rows_to_csv(rows, ","), keys, False


def rows_to_csv(rows, delim, bom=False):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delim, lineterminator="\n")
    w.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


CSV_BUILDERS = {"benefits-table": csv_benefits, "fleet-inventory": csv_fleet,
                "price-list-2026": csv_prices, "office-locations": csv_offices}


# ----------------------------------------------------------------------------
# Cross-document plants and absent topics (quotes are resolved from per-doc facts)
# ----------------------------------------------------------------------------
# Each entry: (topic, [(doc file, substring identifying exactly one fact in that doc), ...])
CONTRADICTIONS = [
    ("Annual leave days (24 vs 27)", [("hr-leave-policy-2024.pdf", "24 working days"), ("hr-leave-policy-2026.pdf", "27 working days")]),
    ("Carry-over (5 days by 31 March vs 8 days by 30 June)", [("hr-leave-policy-2024.pdf", "carried over"), ("hr-leave-policy-2026.pdf", "carried over")]),
    ("Sick-leave certificate threshold (3 vs 5 days)", [("hr-leave-policy-2024.pdf", "medical certificate"), ("hr-leave-policy-2026.pdf", "medical certificate")]),
    ("Parental leave (12 weeks at 80% vs 16 weeks at 100%)", [("hr-leave-policy-2024.pdf", "Parental leave is"), ("hr-leave-policy-2026.pdf", "Parental leave is")]),
    ("Leave request notice (14 calendar days vs 10 working days)", [("hr-leave-policy-2024.pdf", "in advance"), ("hr-leave-policy-2026.pdf", "in advance")]),
    ("Public holidays (11 vs 12)", [("hr-leave-policy-2024.pdf", "public holidays"), ("hr-leave-policy-2026.pdf", "public holidays")]),
    ("Unpaid leave cap (30 vs 60 days)", [("hr-leave-policy-2024.pdf", "Unpaid leave"), ("hr-leave-policy-2026.pdf", "Unpaid leave")]),
    ("Bereavement leave (3 vs 5 days)", [("hr-leave-policy-2024.pdf", "Bereavement"), ("hr-leave-policy-2026.pdf", "Bereavement")]),
    ("Manager approval ceiling (10 vs 15 days)", [("hr-leave-policy-2024.pdf", "Line managers"), ("hr-leave-policy-2026.pdf", "Line managers")]),
    ("First-year accrual (2 vs 2.25 days per month)", [("hr-leave-policy-2024.pdf", "per completed month"), ("hr-leave-policy-2026.pdf", "per completed month")]),
    ("Stale handbook: onboarding handbook still quotes the 2024 entitlement", [("onboarding-handbook.pdf", "24 days of annual leave"), ("hr-leave-policy-2026.pdf", "27 working days")]),
    ("Domestic meal cap (EUR 45 vs EUR 48)", [("expense-policy.pdf", "domestic travel"), ("travel-guidelines.docx", "domestic travel")]),
    ("Hotel cap in Tier A cities (EUR 160 vs EUR 175)", [("expense-policy.pdf", "Tier A"), ("travel-guidelines.docx", "Tier A")]),
    ("Fix date for INV-2047 (postmortem due date 2026-03-31 vs changelog release 2026-04-09)", [("incident-2025-11-postmortem.txt", "INV-2047"), ("changelog.txt", "INV-2047")]),
]
NEAR_DUPLICATES = [
    ("Breach reporting deadline (24 hours internal vs 48 hours processor-to-controller)", [("security-incident-response.pdf", "Data Protection Officer"), ("data-processing-addendum.docx", "without undue delay")]),
    ("Emergency control-room phone number (0199 vs 0198)", [("onboarding-handbook.pdf", "control room"), ("facility-safety-notice-scanned.pdf", "control room")]),
    ("Forklift speed limit (8 km/h indoors in notice vs yard limit in ops manual)", [("facility-safety-notice-scanned.pdf", "Forklifts"), ("ops-manual.md", "Forklift")]),
]
IDENTICAL = [
    ("Identical paragraph: confidentiality notice", [("vendor-sla.pdf", "property of Northwind Logistics B.V."), ("data-processing-addendum.docx", "property of Northwind Logistics B.V.")]),
    ("Identical paragraph: emergency contact", [("onboarding-handbook.pdf", "life-threatening"), ("security-incident-response.pdf", "life-threatening")]),
]
JOINS = [
    ("Who manages the depot where vehicle NW-T-0417 is based (fleet row gives D-HAM, office row gives manager)", [("fleet-inventory.csv", "NW-T-0417"), ("office-locations.csv", "D-HAM")]),
    ("Service credit due for November 2025 (postmortem availability 99.31%, SLA credit tier for 99.00% to 99.49%)", [("incident-2025-11-postmortem.txt", "99.31%"), ("vendor-sla.pdf", "99.00% to 99.49%")]),
    ("Consumables revenue for NW-PAL-120 (Q3 units x price list; 184,300 x 14,90 = 2,746,070)", [("q3-operations-report.pdf", "NW-PAL-120"), ("price-list-2026.csv", "NW-PAL-120")]),
    ("Health insurance employer share (handbook gives plan code BEN-HLT-01, benefits table gives employer_pays_pct)", [("onboarding-handbook.pdf", "BEN-HLT-01"), ("benefits-table.csv", "BEN-HLT-01")]),
    ("Data breach notice: MSA references the DPA by number (DPA-2026-011)", [("master-services-agreement.docx", "DPA-2026-011"), ("data-processing-addendum.docx", "DPA-2026-011")]),
    ("Fuel surcharge (MSA table, price list, minutes all state 6.5%)", [("master-services-agreement.docx", "Fuel surcharge"), ("price-list-2026.csv", "FUEL-SUR"), ("meeting-minutes-2026-03.docx", "fuel surcharge")]),
    ("Rate limit and release (API spec says 600 req/min; changelog v4.2.0 raised it)", [("api-spec.md", "600 requests"), ("changelog.txt", "600 requests")]),
]
ABSENT = [
    ("Stock options, equity or share schemes", r"stock|equity|share scheme|\besop\b|\brsu"),
    ("Parental leave (or any leave) for contractors / self-employed workers", r"contractor|freelanc|self-employed"),
    ("Sabbaticals and career breaks", r"sabbatical|career break"),
    ("Relocation assistance", r"relocat"),
    ("Salary bands, pay grades, bonuses and commissions", r"salar|bonus|commission|pay grade|pay band|incentive"),
    ("Pension schemes and employer pension contributions", r"pension|retirement"),
    ("Remote work, working from home and home-office allowances", r"remote work|work from home|home.office|telework|hybrid"),
    ("Dress code or uniform policy", r"dress code|attire|uniform"),
    ("Carbon, emissions or sustainability targets", r"carbon|emission|\bco2\b|sustainab|\besg\b"),
    ("Customs duties and tariff rates", r"tariff|duties|\bduty\b"),
    ("Mergers, acquisitions or IPO plans", r"acquir|acquisition|merger|\bipo\b"),
    ("Smoking policy", r"smok"),
    ("Driver working-time, tachograph and rest-period rules", r"tachograph|driving time|rest period"),
    ("Whistleblowing channel", r"whistle"),
    ("Maternity leave for the Austin (US) office", r"maternity|\bfmla\b"),
]


# ----------------------------------------------------------------------------
def fact_lines_csv(data: bytes, keys, ):
    text = data.decode("utf-8-sig")
    lines = text.split("\n")
    out = []
    for k in keys:
        hit = [ln for ln in lines if ln.startswith(k)]
        assert len(hit) == 1, (k, hit)
        out.append(hit[0])
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.iterdir():
        if f.is_file():
            f.unlink()
    facts: dict[str, list[str]] = {}
    order: list[str] = []
    alltext: list[str] = []

    def reg(name, fl):
        facts[name] = fl
        order.append(name)

    for stem in PDFS:
        doc = parse(SRC / (stem + ".md"))
        render_pdf(OUT / (stem + ".pdf"), doc)
        reg(stem + ".pdf", doc["facts"])
        alltext.append((SRC / (stem + ".md")).read_text())
    sdoc = parse(SRC / (SCANNED + ".md"))
    render_scanned(OUT / (SCANNED + ".pdf"), OUT / (SCANNED + ".truth.txt"), sdoc)
    reg(SCANNED + ".pdf", sdoc["facts"])
    alltext.append((SRC / (SCANNED + ".md")).read_text())
    for stem in DOCXS:
        doc = parse(SRC / (stem + ".md"))
        render_docx(OUT / (stem + ".docx"), doc)
        reg(stem + ".docx", doc["facts"])
        alltext.append((SRC / (stem + ".md")).read_text())
    for stem in CSVS:
        data, keys, _ = CSV_BUILDERS[stem]()
        (OUT / (stem + ".csv")).write_bytes(data)
        reg(stem + ".csv", fact_lines_csv(data, keys))
        alltext.append(data.decode("utf-8-sig"))
    for stem in MDS:
        doc = parse(SRC / (stem + ".md"))
        render_md(OUT / (stem + ".md"), doc)
        reg(stem + ".md", doc["facts"])
        alltext.append((SRC / (stem + ".md")).read_text())
    for stem in TXTS:
        doc = parse(SRC / (stem + ".md"))
        (OUT / (stem + ".txt")).write_text(txt_text(doc), encoding="utf-8")
        reg(stem + ".txt", doc["facts"])
        alltext.append((SRC / (stem + ".md")).read_text())

    # absent-topic guard: fail the build if a "deliberately absent" topic is mentioned anywhere
    blob = "\n".join(alltext).lower()
    for topic, rx in ABSENT:
        m = re.search(rx, blob)
        assert not m, "absent topic '%s' appears in corpus: %r" % (topic, m.group(0))

    def resolve(doc, sub):
        hits = [q for q in facts[doc] if sub.lower() in q.lower()]
        assert len(hits) == 1, (doc, sub, hits)
        return hits[0]

    def xline(topic, items):
        return "- " + topic + " :: " + " :: ".join("%s :: %s" % (d, resolve(d, s)) for d, s in items)

    # fixed-width derived stats from fleet csv
    fleet = list(csv.DictReader(io.StringIO((OUT / "fleet-inventory.csv").read_text())))
    from collections import Counter
    per_depot = Counter(r["home_depot"] for r in fleet)
    per_status = Counter(r["status"] for r in fleet)

    md = []
    md.append("Human review status: NOT YET REVIEWED — a person must verify every fact against the source before this dataset is used for milestone reports.")
    md.append("")
    md.append("# Northwind Logistics evaluation corpus: planted facts")
    md.append("")
    md.append("Generated by `fixtures/build_corpus.py` from `fixtures/corpus_src/`. Northwind Logistics is fictional; all people, numbers and identifiers are invented.")
    md.append("Each line below is a quote that must occur exactly once in its document after normalisation (NFKC, lowercase, whitespace collapsed, backticks removed).")
    md.append("Table rows are shown with ` | ` between cells (DOCX and PDF tables are extracted that way; CSV rows are the raw line).")
    md.append("The scanned PDF has no text layer; its quotes are checked against `facility-safety-notice-scanned.truth.txt`.")
    md.append("")
    md.append("## Per-document facts")
    total = 0
    for name in order:
        md.append("")
        md.append("### " + name)
        md.append("")
        for q in facts[name]:
            assert len(q) <= 200, (name, q)
            md.append("- " + q)
            total += 1
    md.append("")
    md.append("## Contradictions")
    md.append("")
    md.append("Format: `topic :: document :: quote :: document :: quote`. These are deliberate; the 2026 policy supersedes the 2024 policy, and the handbook is stale.")
    md.append("")
    md += [xline(t, i) for t, i in CONTRADICTIONS]
    md.append("")
    md.append("## Near-duplicate sentences that differ in one detail")
    md.append("")
    md += [xline(t, i) for t, i in NEAR_DUPLICATES]
    md.append("")
    md.append("## Identical paragraphs in two places")
    md.append("")
    md += [xline(t, i) for t, i in IDENTICAL]
    md.append("")
    md.append("## Facts that need a join across documents")
    md.append("")
    md += [xline(t, i) for t, i in JOINS]
    md.append("")
    md.append("## Derived facts (computed by the build script, not quotable)")
    md.append("")
    md.append("- fleet-inventory.csv has %d vehicles. Per home depot: %s." % (len(fleet), ", ".join("%s=%d" % kv for kv in sorted(per_depot.items()))))
    md.append("- fleet-inventory.csv status counts: %s." % ", ".join("%s=%d" % kv for kv in sorted(per_status.items())))
    md.append("- Q3 2026 hub parcels sum to 15,015 thousand (7 hubs); consumables revenue NW-PAL-120 = 184,300 x EUR 14.90 = EUR 2,746,070.")
    md.append("- November 2025 Helios downtime: 221 + 77 = 298 minutes of 43,200, i.e. 99.31% availability, which falls in the 10% service-credit tier (EUR 1,840 on a EUR 18,400 monthly fee).")
    md.append("")
    md.append("## Deliberately absent topics")
    md.append("")
    md.append("No document mentions the following. Questions about them must be answered with insufficient evidence. The build fails if any of these patterns appears in any source text.")
    md.append("")
    for t, rx in ABSENT:
        md.append("- %s (pattern: `%s`)" % (t, rx))
    md.append("")
    (OUT / "FACTS.md").write_text("\n".join(md), encoding="utf-8")
    print("built %d documents, %d per-document facts" % (len(order), total))


if __name__ == "__main__":
    main()
