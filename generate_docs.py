"""Generate fake lumber-mill paperwork as PDFs, plus the correct answer for each.

Everything here is invented: the company, the vendors, the numbers. A few
documents are deliberately defective (missing date, totals that don't add up,
a resent duplicate) because real paperwork is messy, and the tool should catch it.

    python generate_docs.py

Writes data/docs/*.pdf and data/truth/*.json (the hand-checkable ground truth).
"""
import argparse
import json
import random
import shutil
from datetime import date, timedelta
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "data" / "docs"
TRUTH = ROOT / "data" / "truth"

BUYER = "Northern Pine Lumber Co."
BUYER_ADDR = "100 Mill Road, Pinecrest, ID 83800"

VENDORS = [
    {"name": "Larch Creek Saw Supply", "addr": "412 Industrial Way, Spokane Valley, WA", "tax": 0.06},
    {"name": "Ponderosa Hydraulics Inc", "addr": "88 Rail Spur Rd, Post Falls, ID", "tax": 0.06},
    {"name": "Inland Fuel & Lube", "addr": "2301 Highway 95, Sandpoint, ID", "tax": 0.0},
    {"name": "Panhandle Industrial Belting", "addr": "17 Foundry St, Coeur d'Alene, ID", "tax": 0.06},
    {"name": "Tamarack Bearing & Chain", "addr": "950 Commerce Dr, Lewiston, ID", "tax": 0.06},
    {"name": "Kootenai Safety Supply", "addr": "6 Depot Ln, Bonners Ferry, ID", "tax": 0.06},
]

# (description, min price, max price, min qty, max qty)
PARTS = [
    ("Bandsaw blade 1.5in x 24ft", 180, 260, 2, 12),
    ("Hydraulic hose 3/4in x 50ft", 95, 140, 1, 6),
    ("Diesel fuel (gal)", 3.80, 4.60, 400, 1500),
    ("Conveyor belting 24in (ft)", 22, 35, 20, 120),
    ("Bearing 6310 2RS", 28, 55, 4, 30),
    ("Chain lube 5gal pail", 62, 90, 2, 10),
    ("Planer knife set", 410, 640, 1, 4),
    ("Safety glasses (box of 12)", 38, 58, 1, 6),
    ("Roller chain #80 (ft)", 9, 16, 30, 150),
]

CUSTOMERS = ["Treeline Truss & Panel", "Big Sky Building Supply", "Harbor Point Lumber Yard",
             "Prairie Home Centers", "Summit Fence & Deck"]
CARRIERS = ["Redline Freight", "Gem State Flatbed", "Long Haul Logistics"]
# (description, min weight per bundle, max weight per bundle)
LUMBER = [
    ("2x4x8 SPF stud, bundle", 2100, 2500),
    ("2x6x10 Douglas Fir #2, bundle", 2600, 3100),
    ("2x10x16 Larch #2, bundle", 3200, 3700),
    ("1x6x8 Cedar fence board, bundle", 1100, 1500),
    ("4x4x8 Treated post, bundle", 2800, 3300),
]

CONTRACTORS = ["Ridgeline Millwright Services", "Tamarack Electric & Controls", "Summit Hydraulic Repair"]
EQUIPMENT = ["Planer #2", "Debarker", "Green chain", "Kiln 3 fan motor", "Sawmill carriage", "Trim saw"]
WO_PARTS = [
    ("Bearing 6310 2RS", 28, 55),
    ("Hydraulic cylinder seal kit", 70, 130),
    ("Drive belt B85", 24, 48),
    ("Contactor 40A", 55, 95),
]


def money(x: float) -> str:
    return f"${x:,.2f}"


def fmt_date(d: date, style: int) -> str:
    if style == 0:
        return d.isoformat()
    if style == 1:
        return d.strftime("%b %d, %Y").replace(" 0", " ")
    return d.strftime("%m/%d/%Y")


def new_pdf() -> FPDF:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()
    pdf.set_margins(18, 16, 18)
    pdf.set_font("Helvetica", size=10)
    return pdf


def line(pdf: FPDF, text: str, size: int = 10, style: str = "", align: str = "L", gap: float = 5.5):
    pdf.set_font("Helvetica", style, size)
    pdf.cell(0, gap, text, align=align, new_x="LMARGIN", new_y="NEXT")


def table(pdf: FPDF, headers: list[str], rows: list[list[str]], widths: list[int], aligns: list[str]):
    pdf.set_font("Helvetica", "B", 10)
    for h, w, a in zip(headers, widths, aligns):
        pdf.cell(w, 7, h, border="B", align=a)
    pdf.ln(7)
    pdf.set_font("Helvetica", "", 10)
    for row in rows:
        for cell, w, a in zip(row, widths, aligns):
            pdf.cell(w, 6.5, cell, align=a)
        pdf.ln(6.5)


def totals_block(pdf: FPDF, rows: list[tuple[str, str]]):
    pdf.ln(4)
    for label, value in rows:
        pdf.set_font("Helvetica", "B" if label.lower().startswith(("total", "amount")) else "", 10)
        pdf.cell(120, 6, "")
        pdf.cell(25, 6, label, align="R")
        pdf.cell(0, 6, value, align="R", new_x="LMARGIN", new_y="NEXT")


# ---------------------------------------------------------------- invoices

def make_invoice(rng, index, vendor, layout, style, when, number=None, defect=None):
    picks = rng.sample(PARTS, rng.randint(2, 5))
    items = []
    for desc, lo, hi, qlo, qhi in picks:
        qty = rng.randint(qlo, qhi)
        price = round(rng.uniform(lo, hi), 2)
        items.append({"description": desc, "quantity": float(qty), "unit_price": price,
                      "amount": round(qty * price, 2)})

    number = number or f"INV-{rng.randint(10000, 99999)}"
    if defect == "bad_line":
        items[rng.randrange(len(items))]["amount"] += rng.choice([40.00, 125.50, -60.25])

    subtotal = round(sum(i["amount"] for i in items), 2)
    tax = round(subtotal * vendor["tax"], 2)
    total = round(subtotal + tax, 2)
    if defect == "bad_total":
        total = round(total + rng.choice([75.00, 212.40, -138.10]), 2)

    printed_date = None if defect == "no_date" else when

    pdf = new_pdf()
    if layout == "A":
        line(pdf, vendor["name"], 16, "B")
        line(pdf, vendor["addr"], 9)
        pdf.ln(4)
        line(pdf, "INVOICE", 14, "B", "R")
        line(pdf, f"Invoice No.: {number}", align="R")
        line(pdf, f"Date: {fmt_date(printed_date, style)}" if printed_date else "Date:", align="R")
        line(pdf, "Terms: Net 30", align="R")
        pdf.ln(3)
        line(pdf, "Bill To:", style="B")
        line(pdf, BUYER)
        line(pdf, BUYER_ADDR)
        pdf.ln(5)
        table(pdf, ["Description", "Qty", "Unit Price", "Amount"],
              [[i["description"], f"{i['quantity']:g}", money(i["unit_price"]), money(i["amount"])] for i in items],
              [90, 20, 32, 32], ["L", "R", "R", "R"])
        rows = [("Subtotal", money(subtotal))]
        if vendor["tax"]:
            rows.append(("Tax", money(tax)))
        rows.append(("Total", money(total)))
        totals_block(pdf, rows)
    else:
        line(pdf, vendor["name"].upper(), 15, "B", "C")
        line(pdf, vendor["addr"], 9, align="C")
        pdf.ln(6)
        line(pdf, f"Inv # {number}")
        line(pdf, f"Invoice Date  {fmt_date(printed_date, style)}" if printed_date else "Invoice Date")
        line(pdf, f"Sold to: {BUYER}, {BUYER_ADDR}")
        pdf.ln(5)
        table(pdf, ["Qty", "Item", "Price", "Ext."],
              [[f"{i['quantity']:g}", i["description"], money(i["unit_price"]), money(i["amount"])] for i in items],
              [20, 90, 32, 32], ["R", "L", "R", "R"])
        rows = [("Merchandise", money(subtotal))]
        if vendor["tax"]:
            rows.append(("Sales tax", money(tax)))
        rows.append(("Amount due", money(total)))
        totals_block(pdf, rows)
    pdf.ln(8)
    line(pdf, "Please remit payment within 30 days. Thank you for your business.", 9)

    expected = {
        "doc_type": "invoice", "doc_number": number,
        "doc_date": printed_date.isoformat() if printed_date else None,
        "counterparty": vendor["name"], "equipment": None,
        "line_items": [{**i, "weight_lb": None} for i in items],
        "subtotal": subtotal, "tax": tax if vendor["tax"] else None, "total": total,
        "total_weight_lb": None,
    }
    return pdf, expected, ("flagged" if defect else "ok")


# ---------------------------------------------------------- bills of lading

def make_bol(rng, index, when, style, defect=None):
    customer = rng.choice(CUSTOMERS)
    carrier = rng.choice(CARRIERS)
    number = f"BOL-{rng.randint(100000, 999999)}"
    items = []
    for desc, wlo, whi in rng.sample(LUMBER, rng.randint(2, 4)):
        bundles = rng.randint(2, 24)
        items.append({"description": desc, "quantity": float(bundles), "unit_price": None,
                      "amount": None, "weight_lb": float(bundles * rng.randint(wlo, whi))})
    total_weight = float(sum(i["weight_lb"] for i in items))
    printed_date = None if defect == "no_date" else when

    pdf = new_pdf()
    line(pdf, "BILL OF LADING", 16, "B")
    line(pdf, f"BOL No.: {number}")
    line(pdf, f"Ship Date: {fmt_date(printed_date, style)}" if printed_date else "Ship Date:")
    line(pdf, f"Carrier: {carrier}")
    pdf.ln(3)
    line(pdf, f"Shipper: {BUYER}, {BUYER_ADDR}")
    line(pdf, f"Consignee: {customer}")
    pdf.ln(5)
    table(pdf, ["Description", "Bundles", "Weight (lb)"],
          [[i["description"], f"{i['quantity']:g}", f"{i['weight_lb']:,.0f}"] for i in items],
          [100, 30, 40], ["L", "R", "R"])
    pdf.ln(4)
    line(pdf, f"Total weight: {total_weight:,.0f} lb", style="B", align="R")
    pdf.ln(6)
    line(pdf, "Carrier acknowledges receipt of the above in apparent good order.", 9)

    expected = {
        "doc_type": "bill_of_lading", "doc_number": number,
        "doc_date": printed_date.isoformat() if printed_date else None,
        "counterparty": customer, "equipment": None, "line_items": items,
        "subtotal": None, "tax": None, "total": None, "total_weight_lb": total_weight,
    }
    return pdf, expected, ("flagged" if defect else "ok")


# -------------------------------------------------------------- work orders

def make_work_order(rng, index, when, style, defect=None):
    contractor = rng.choice(CONTRACTORS)
    equipment = rng.choice(EQUIPMENT)
    number = f"WO-{rng.randint(2000, 9999)}"
    trade = rng.choice(["millwright", "electrician", "hydraulic tech"])
    hours = float(rng.randint(2, 14))
    rate = float(rng.choice([85, 95, 105, 120]))
    items = [{"description": f"Labor: {trade}", "quantity": hours, "unit_price": rate,
              "amount": round(hours * rate, 2), "weight_lb": None}]
    for desc, lo, hi in rng.sample(WO_PARTS, rng.randint(1, 3)):
        qty = float(rng.randint(1, 6))
        price = round(rng.uniform(lo, hi), 2)
        items.append({"description": desc, "quantity": qty, "unit_price": price,
                      "amount": round(qty * price, 2), "weight_lb": None})
    total = round(sum(i["amount"] for i in items), 2)
    if defect == "bad_total":
        total = round(total + rng.choice([90.00, 305.75, -150.20]), 2)

    pdf = new_pdf()
    line(pdf, "WORK ORDER", 16, "B")
    line(pdf, f"WO No.: {number}")
    line(pdf, f"Completed: {fmt_date(when, style)}")
    line(pdf, f"Performed by: {contractor}")
    line(pdf, f"Equipment: {equipment}")
    line(pdf, f"Requested by: {BUYER} maintenance")
    pdf.ln(5)
    table(pdf, ["Description", "Qty / Hrs", "Rate", "Amount"],
          [[i["description"], f"{i['quantity']:g}", money(i["unit_price"]), money(i["amount"])] for i in items],
          [88, 24, 30, 32], ["L", "R", "R", "R"])
    totals_block(pdf, [("Total due", money(total))])
    pdf.ln(8)
    line(pdf, "Work performed per request. Equipment returned to service.", 9)

    expected = {
        "doc_type": "work_order", "doc_number": number, "doc_date": when.isoformat(),
        "counterparty": contractor, "equipment": equipment, "line_items": items,
        "subtotal": None, "tax": None, "total": total, "total_weight_lb": None,
    }
    return pdf, expected, ("flagged" if defect else "ok")


# --------------------------------------------------------------------- main

def write(name: str, built) -> None:
    pdf, expected, status = built
    pdf.output(str(DOCS / f"{name}.pdf"))
    (TRUTH / f"{name}.json").write_text(
        json.dumps({"expected": expected, "expected_status": status}, indent=2), encoding="utf-8")


def main() -> None:
    global DOCS, TRUTH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "data",
                        help="writes <out-dir>/docs and <out-dir>/truth")
    args = parser.parse_args()
    DOCS, TRUTH = args.out_dir / "docs", args.out_dir / "truth"

    rng = random.Random(args.seed)
    for folder in (DOCS, TRUTH):
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)

    start = date(2026, 6, 1)

    def random_date():
        return start + timedelta(days=rng.randint(0, 135))

    # 14 invoices: 10 clean, 1 missing date, 1 wrong total, 1 wrong line, 1 resent duplicate
    defects = [None] * 10 + ["no_date", "bad_total", "bad_line"]
    rng.shuffle(defects)
    first_clean = None
    for n, defect in enumerate(defects, start=1):
        vendor = rng.choice(VENDORS)
        built = make_invoice(rng, n, vendor, rng.choice("AB"), rng.randint(0, 2), random_date(), defect=defect)
        write(f"invoice_{n:02d}", built)
        if defect is None and first_clean is None:
            first_clean = (n, vendor, built[1])

    # the resend: same vendor, same invoice number, same contents
    n, vendor, expected = first_clean
    # rendered from the original's truth, so the resent PDF carries identical contents
    pdf = new_pdf()
    line(pdf, vendor["name"], 16, "B")
    line(pdf, vendor["addr"], 9)
    pdf.ln(4)
    line(pdf, "INVOICE", 14, "B", "R")
    line(pdf, f"Invoice No.: {expected['doc_number']}", align="R")
    line(pdf, f"Date: {expected['doc_date']}", align="R")
    line(pdf, "Terms: Net 30", align="R")
    pdf.ln(3)
    line(pdf, "Bill To:", style="B")
    line(pdf, BUYER)
    line(pdf, BUYER_ADDR)
    pdf.ln(5)
    table(pdf, ["Description", "Qty", "Unit Price", "Amount"],
          [[i["description"], f"{i['quantity']:g}", money(i["unit_price"]), money(i["amount"])]
           for i in expected["line_items"]],
          [90, 20, 32, 32], ["L", "R", "R", "R"])
    rows = [("Subtotal", money(expected["subtotal"]))]
    if expected["tax"] is not None:
        rows.append(("Tax", money(expected["tax"])))
    rows.append(("Total", money(expected["total"])))
    totals_block(pdf, rows)
    pdf.ln(8)
    line(pdf, "Please remit payment within 30 days. Thank you for your business.", 9)
    write("invoice_15_resend", (pdf, expected, "flagged"))

    # 8 bills of lading, one missing its date
    bol_defects = [None] * 7 + ["no_date"]
    rng.shuffle(bol_defects)
    for n, defect in enumerate(bol_defects, start=1):
        write(f"bol_{n:02d}", make_bol(rng, n, random_date(), rng.randint(0, 2), defect))

    # 8 work orders, one with a wrong total
    wo_defects = [None] * 7 + ["bad_total"]
    rng.shuffle(wo_defects)
    for n, defect in enumerate(wo_defects, start=1):
        write(f"workorder_{n:02d}", make_work_order(rng, n, random_date(), rng.randint(0, 2), defect))

    print(f"Wrote {len(list(DOCS.glob('*.pdf')))} PDFs to {DOCS}")


if __name__ == "__main__":
    main()
