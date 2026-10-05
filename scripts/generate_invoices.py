"""
Generate a realistic, messy set of sample invoices plus an exact answer key.

Why synthetic? Because we *know* the right answer for every field, we can
score the AI's extraction precisely. The messiness is deliberate:
  - 6 different vendor layouts (labels, positions and wording all differ)
  - mixed date formats (2026-03-05, 03/05/2026, 5 March 2026, 05.03.2026)
  - European number formats (1.234,56 EUR) next to US ones ($1,234.56)
  - distractors: PO numbers, customer IDs, "ship to" vs "bill to", discounts
  - payment terms only ("Net 30") instead of an explicit due date
  - a two-page invoice where the total is on page 2
  - "scanned" copies: rotated, noisy, image-only (no selectable text)

Two sets:
  practice  data/invoices/ + data/ground_truth.json            (tune on this)
  test      data/test/invoices/ + data/test/ground_truth.json  (held out: different
            random invoices, plus 2 layouts that never appear in the practice set)

Run:  python scripts/generate_invoices.py            (both sets)
      python scripts/generate_invoices.py --set test
"""
from __future__ import annotations

import io
import json
import random
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import A4, letter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
SETS = {
    # name: (invoice folder, answer key, random seed, layouts with counts)
    "practice": (ROOT / "data" / "invoices", ROOT / "data" / "ground_truth.json", 7,
                 {"classic": 5, "modern": 5, "receipt": 5, "euro": 5, "multipage": 5, "lettered": 5}),
    "test": (ROOT / "data" / "test" / "invoices", ROOT / "data" / "test" / "ground_truth.json", 2026,
             {"classic": 4, "modern": 4, "receipt": 4, "euro": 3, "multipage": 3, "lettered": 4,
              "form": 4, "statement": 4}),  # form + statement: layouts the practice set never shows
}

VENDORS = [
    ("Northwind Office Supply", "418 Commerce Dr, Dallas, TX 75201"),
    ("Bluebird Logistics LLC", "77 Harbor Way, Oakland, CA 94607"),
    ("Cedar & Pine Facilities", "1200 Elm St, Minneapolis, MN 55403"),
    ("Meridian IT Services", "55 Market St, Suite 900, Chicago, IL 60606"),
    ("Kestrel Print Works", "9 Mill Rd, Portland, OR 97214"),
    ("Halvorsen Industrietechnik GmbH", "Hafenstrasse 14, 20457 Hamburg, Germany"),
    ("Atlas Janitorial Co.", "310 Sunset Blvd, Phoenix, AZ 85004"),
    ("Brightline Electrical", "62 Foundry Ln, Pittsburgh, PA 15222"),
    ("Verde Catering Group", "15 Plaza Mayor, Austin, TX 78701"),
    ("Summit Safety Equipment", "880 Ridge Rd, Denver, CO 80202"),
]
CUSTOMERS = [
    "Lakeside Medical Partners", "Ridgeview Manufacturing Inc.", "Oakmont School District",
    "Prairie Credit Union", "Harborview Hotel Group", "Granite Peak Construction",
    "Silverline Dental", "Riverbend Foods Co.",
]
ITEMS = [
    ("Copy paper, 10 ream case", 42.99), ("Toner cartridge HP 58A", 89.50),
    ("Pallet freight - LTL", 315.00), ("Fuel surcharge", 38.25),
    ("HVAC quarterly maintenance", 640.00), ("Air filter 20x25x1 (12 pk)", 54.00),
    ("Managed IT support - monthly", 1850.00), ("Laptop setup & imaging", 125.00),
    ("Business cards (500)", 64.00), ("Vinyl banner 3x8 ft", 148.00),
    ("Hydraulic coupling DN25", 212.40), ("Service technician hour", 95.00),
    ("Floor stripping & waxing (sq ft)", 0.42), ("Restroom supplies kit", 76.80),
    ("LED panel retrofit", 189.00), ("Electrician labor (hr)", 110.00),
    ("Boxed lunch", 14.75), ("Coffee service (per guest)", 4.50),
    ("Hard hat, Class E", 23.95), ("Safety glasses (box of 12)", 41.00),
]

D2 = Decimal("0.01")


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(D2, rounding=ROUND_HALF_UP)


def fmt_us(d: Decimal, symbol: str = "$") -> str:
    return f"{symbol}{d:,.2f}"


def fmt_eu(d: Decimal) -> str:
    s = f"{d:,.2f}"  # 1,234.56 -> 1.234,56
    return s.replace(",", "X").replace(".", ",").replace("X", ".") + " EUR"


def fmt_date(d: date, style: str) -> str:
    return {
        "iso": d.isoformat(),
        "us": d.strftime("%m/%d/%Y"),
        "long": d.strftime("%B %-d, %Y"),
        "dmy_long": d.strftime("%-d %B %Y"),
        "eu_dot": d.strftime("%d.%m.%Y"),
        "short": d.strftime("%b %d, %Y"),
        "dash_mon": d.strftime("%d-%b-%Y"),
        "slash_ymd": d.strftime("%Y/%m/%d"),
    }[style]


def make_invoice(rng: random.Random, idx: int, layout: str) -> dict:
    vendor, vaddr = VENDORS[idx % len(VENDORS)] if layout != "euro" else VENDORS[5]
    if layout != "euro" and vendor == VENDORS[5][0]:
        vendor, vaddr = VENDORS[0]
    customer = rng.choice(CUSTOMERS)
    n_items = rng.randint(14, 20) if layout == "multipage" else rng.randint(1, 6)
    items = []
    for desc, price in rng.sample(ITEMS, k=min(n_items, len(ITEMS))):
        qty = rng.choice([1, 1, 2, 3, 4, 5, 10, 12, 25]) if price > 1 else rng.choice([1200, 2500, 4000])
        unit = money(price)
        items.append({"description": desc, "quantity": qty, "unit_price": float(unit),
                      "amount": float(money(unit * qty))})
    subtotal = money(sum(Decimal(str(i["amount"])) for i in items))
    discount = money(subtotal * Decimal("0.05")) if rng.random() < 0.25 else Decimal("0.00")
    tax_rate = {"euro": Decimal("0.19")}.get(layout, rng.choice([Decimal("0"), Decimal("0.0625"), Decimal("0.0825"), Decimal("0.07")]))
    tax = money((subtotal - discount) * tax_rate)
    total = money(subtotal - discount + tax)
    inv_date = date(2026, 1, 1) + timedelta(days=rng.randint(0, 240))
    terms_days = rng.choice([15, 30, 45])
    due = inv_date + timedelta(days=terms_days)
    prefix = {"classic": "INV-", "modern": "", "receipt": "R", "euro": "RE-2026-", "multipage": "MIS-", "lettered": "A",
              "form": "PI/26/", "statement": "ST-"}[layout]
    inv_no = f"{prefix}{rng.randint(1000, 99999)}"
    return {
        "layout": layout,
        "vendor_name": vendor, "vendor_address": vaddr,
        "invoice_number": inv_no,
        "po_number": f"PO-{rng.randint(10000, 99999)}",
        "customer_id": f"C{rng.randint(100, 999)}",
        "invoice_date": inv_date, "due_date": due, "terms_days": terms_days,
        "terms_only": layout in ("lettered",) or rng.random() < 0.15,
        "bill_to": customer,
        "ship_to": rng.choice([c for c in CUSTOMERS if c != customer]),
        "currency": "EUR" if layout == "euro" else "USD",
        "subtotal": subtotal, "discount": discount, "tax_rate": tax_rate, "tax": tax, "total": total,
        "line_items": items,
    }


# ----------------------------------------------------------------- layouts
def bold(font):
    return {"Times-Roman": "Times-Bold", "Courier": "Courier-Bold"}.get(font, font + "-Bold")


def table(c, inv, x, y, cols, fmt, font="Helvetica", size=9, row_h=14, header=True, max_y=60):
    """Draw line items; returns new y. cols = [(label, key, x_offset, align)]"""
    if header:
        c.setFont(bold(font), size)
        for label, _, dx, al in cols:
            (c.drawRightString if al == "r" else c.drawString)(x + dx, y, label)
        y -= 4
        c.line(x, y, x + 500, y)
        y -= row_h
    c.setFont(font, size)
    for it in inv["line_items"]:
        for _, key, dx, al in cols:
            v = it[key]
            s = fmt(money(v)) if key in ("unit_price", "amount") else str(v)
            (c.drawRightString if al == "r" else c.drawString)(x + dx, y, s)
        y -= row_h
    return y


def totals_block(c, inv, x, y, fmt, labels, font="Helvetica", size=10):
    c.setFont(font, size)
    rows = [(labels[0], inv["subtotal"])]
    if inv["discount"] > 0:
        rows.append(("Discount (5%)", -inv["discount"]))
    if inv["tax"] > 0 or inv["layout"] == "euro":
        pct = (inv["tax_rate"] * 100).normalize()
        rows.append((f"{labels[1]} ({pct}%)", inv["tax"]))
    rows.append((labels[2], inv["total"]))
    for i, (lab, val) in enumerate(rows):
        if i == len(rows) - 1:
            c.setFont(bold(font), size + 1)
        c.drawString(x, y, lab)
        c.drawRightString(x + 190, y, fmt(val) if val >= 0 else "-" + fmt(-val))
        y -= 16
    return y


def due_text(inv, style):
    if inv["terms_only"]:
        return ("Terms", f"Net {inv['terms_days']}")
    return ("Due Date", fmt_date(inv["due_date"], style))


def draw_classic(c, inv):
    W, H = letter
    c.setFont("Helvetica-Bold", 16); c.drawString(50, H - 60, inv["vendor_name"])
    c.setFont("Helvetica", 9); c.drawString(50, H - 74, inv["vendor_address"])
    c.setFont("Helvetica-Bold", 22); c.drawRightString(W - 50, H - 60, "INVOICE")
    c.setFont("Helvetica", 10)
    lab, val = due_text(inv, "us")
    for i, (k, v) in enumerate([("Invoice #", inv["invoice_number"]), ("Date", fmt_date(inv["invoice_date"], "us")),
                                (lab, val), ("PO Number", inv["po_number"])]):
        c.drawRightString(W - 140, H - 90 - i * 14, k + ":"); c.drawRightString(W - 50, H - 90 - i * 14, v)
    c.setFont("Helvetica-Bold", 10); c.drawString(50, H - 150, "BILL TO"); c.drawString(300, H - 150, "SHIP TO")
    c.setFont("Helvetica", 10); c.drawString(50, H - 164, inv["bill_to"]); c.drawString(300, H - 164, inv["ship_to"])
    y = table(c, inv, 50, H - 210, [("Description", "description", 0, "l"), ("Qty", "quantity", 300, "r"),
                                    ("Unit Price", "unit_price", 400, "r"), ("Amount", "amount", 500, "r")], fmt_us)
    totals_block(c, inv, 360, y - 20, fmt_us, ("Subtotal", "Sales Tax", "TOTAL DUE"))


def draw_modern(c, inv):
    W, H = letter
    c.setFont("Helvetica", 28); c.drawString(50, H - 70, "Invoice")
    c.setFont("Helvetica", 9)
    c.drawRightString(W - 50, H - 50, inv["vendor_name"].upper()); c.drawRightString(W - 50, H - 62, inv["vendor_address"])
    lab, val = due_text(inv, "long")
    pairs = [("Inv No.", inv["invoice_number"]), ("Issued", fmt_date(inv["invoice_date"], "long")), (lab, val),
             ("Customer ID", inv["customer_id"]), ("Invoice to", inv["bill_to"])]
    for i, (k, v) in enumerate(pairs):
        c.setFont("Helvetica", 8); c.setFillGray(0.45); c.drawString(50 + (i % 3) * 170, H - 110 - (i // 3) * 34, k.upper())
        c.setFillGray(0); c.setFont("Helvetica", 11); c.drawString(50 + (i % 3) * 170, H - 124 - (i // 3) * 34, v)
    y = table(c, inv, 50, H - 210, [("ITEM", "description", 0, "l"), ("QTY", "quantity", 310, "r"),
                                    ("RATE", "unit_price", 400, "r"), ("LINE TOTAL", "amount", 500, "r")], fmt_us, size=10)
    totals_block(c, inv, 360, y - 20, fmt_us, ("Subtotal", "Tax", "Balance Due"))
    c.setFont("Helvetica-Oblique", 8); c.drawString(50, 60, "Thank you for your business. Please reference the invoice number with payment.")


def draw_receipt(c, inv):
    W, H = 300, 700
    c.setPageSize((W, H))
    c.setFont("Courier-Bold", 12); c.drawCentredString(W / 2, H - 40, inv["vendor_name"])
    c.setFont("Courier", 8); c.drawCentredString(W / 2, H - 52, inv["vendor_address"])
    c.setFont("Courier", 9)
    lab, val = due_text(inv, "short")
    lines = [f"Receipt No. {inv['invoice_number']}", f"Date: {fmt_date(inv['invoice_date'], 'short')}",
             f"{lab}: {val}", f"Acct: {inv['bill_to']}", "-" * 44]
    y = H - 80
    for ln in lines:
        c.drawString(20, y, ln); y -= 12
    for it in inv["line_items"]:
        c.drawString(20, y, it["description"][:30]); y -= 11
        c.drawString(30, y, f"{it['quantity']} @ {money(it['unit_price']):,.2f}")
        c.drawRightString(W - 20, y, f"{money(it['amount']):,.2f}"); y -= 13
    c.drawString(20, y, "-" * 44); y -= 14
    rows = [("SUBTOTAL", inv["subtotal"])]
    if inv["discount"] > 0: rows.append(("DISC", -inv["discount"]))
    if inv["tax"] > 0: rows.append(("TAX", inv["tax"]))
    rows.append(("AMOUNT DUE", inv["total"]))
    for k, v in rows:
        c.drawString(20, y, k); c.drawRightString(W - 20, y, f"{v:,.2f}"); y -= 12
    c.drawString(20, y - 10, "ALL AMOUNTS IN USD")


def draw_euro(c, inv):
    W, H = A4
    c.setPageSize(A4)
    c.setFont("Times-Bold", 15); c.drawString(50, H - 60, inv["vendor_name"])
    c.setFont("Times-Roman", 9); c.drawString(50, H - 73, inv["vendor_address"] + "  |  USt-IdNr. DE298374651")
    c.setFont("Times-Roman", 10); c.drawString(50, H - 120, "Invoice recipient:"); c.drawString(50, H - 133, inv["bill_to"])
    lab, val = due_text(inv, "eu_dot")
    c.setFont("Times-Bold", 13); c.drawString(50, H - 175, f"Invoice / Rechnung  {inv['invoice_number']}")
    c.setFont("Times-Roman", 10)
    c.drawRightString(W - 50, H - 120, f"Invoice date: {fmt_date(inv['invoice_date'], 'eu_dot')}")
    c.drawRightString(W - 50, H - 133, f"{'Payable by' if lab == 'Due Date' else 'Terms'}: {val}")
    c.drawRightString(W - 50, H - 146, f"Your order: {inv['po_number']}")
    y = table(c, inv, 50, H - 210, [("Pos. / Description", "description", 0, "l"), ("Qty", "quantity", 290, "r"),
                                    ("Unit price", "unit_price", 390, "r"), ("Net amount", "amount", 490, "r")],
              fmt_eu, font="Times-Roman", size=10)
    totals_block(c, inv, 300, y - 20, fmt_eu, ("Net total", "VAT", "Gross total payable"), font="Times-Roman")


def draw_multipage(c, inv):
    W, H = letter
    def header(page):
        c.setFont("Helvetica-Bold", 13); c.drawString(50, H - 50, inv["vendor_name"])
        c.setFont("Helvetica", 9); c.drawRightString(W - 50, H - 50, f"Invoice {inv['invoice_number']}  -  Page {page} of 2")
    header(1)
    c.setFont("Helvetica", 10)
    lab, val = due_text(inv, "iso")
    c.drawString(50, H - 80, f"Invoice date: {fmt_date(inv['invoice_date'], 'iso')}    {lab}: {val}")
    c.drawString(50, H - 95, f"Sold to: {inv['bill_to']}    Ship to: {inv['ship_to']}    Ref: {inv['po_number']}")
    first, rest = inv["line_items"][:10], inv["line_items"][10:]
    cols = [("Description", "description", 0, "l"), ("Qty", "quantity", 300, "r"),
            ("Each", "unit_price", 400, "r"), ("Ext. Price", "amount", 500, "r")]
    y = table(c, {**inv, "line_items": first}, 50, H - 130, cols, fmt_us, row_h=22)
    c.setFont("Helvetica-Oblique", 9); c.drawString(50, y - 10, "Continued on next page...")
    c.showPage(); header(2)
    y = table(c, {**inv, "line_items": rest}, 50, H - 90, cols, fmt_us, row_h=22)
    totals_block(c, inv, 360, y - 20, fmt_us, ("Merchandise total", "Tax", "Invoice Total"))


def draw_lettered(c, inv):
    """Prose-style invoice from a small contractor: amounts embedded in sentences."""
    W, H = letter
    c.setFont("Times-Bold", 14); c.drawString(60, H - 70, inv["vendor_name"])
    c.setFont("Times-Roman", 10); c.drawString(60, H - 84, inv["vendor_address"])
    c.setFont("Times-Roman", 11)
    y = H - 130
    lines = [fmt_date(inv["invoice_date"], "dmy_long"), "", f"To: Accounts Payable, {inv['bill_to']}", "",
             f"Re: Statement for services, our reference {inv['invoice_number']} (your PO {inv['po_number']})", "",
             "For work completed this period we have billed the following:"]
    for ln in lines:
        c.drawString(60, y, ln); y -= 15
    for it in inv["line_items"]:
        c.drawString(75, y, f"- {it['description']}: {it['quantity']} x {fmt_us(money(it['unit_price']))} = {fmt_us(money(it['amount']))}")
        y -= 15
    y -= 8
    s = f"This comes to {fmt_us(inv['subtotal'])}"
    if inv["discount"] > 0:
        s += f", less a 5% loyalty discount of {fmt_us(inv['discount'])}"
    if inv["tax"] > 0:
        s += f", plus {(inv['tax_rate']*100).normalize()}% sales tax of {fmt_us(inv['tax'])}"
    c.drawString(60, y, s + "."); y -= 15
    c.drawString(60, y, f"Please remit {fmt_us(inv['total'])} within {inv['terms_days']} days of the date above."); y -= 30
    c.drawString(60, y, "With thanks,"); c.drawString(60, y - 15, "Accounts Receivable")


def draw_form(c, inv):
    """Boxed 'form' layout with unusual labels and day-month-name dates. Test set only."""
    W, H = letter
    c.setFont("Helvetica-Bold", 18); c.drawCentredString(W / 2, H - 55, "PURCHASE INVOICE")
    boxes = [("SUPPLIER", inv["vendor_name"]), ("DOCUMENT NO.", inv["invoice_number"]),
             ("DOCUMENT DATE", fmt_date(inv["invoice_date"], "dash_mon")),
             ("PAYMENT DUE", f"Net {inv['terms_days']} days" if inv["terms_only"] else fmt_date(inv["due_date"], "dash_mon")),
             ("CUSTOMER", inv["bill_to"]), ("DELIVERY ADDRESS", inv["ship_to"])]
    for i, (lab, val) in enumerate(boxes):
        x, y = 50 + (i % 2) * 260, H - 110 - (i // 2) * 48
        c.rect(x, y - 12, 250, 40)
        c.setFont("Helvetica", 7); c.drawString(x + 5, y + 18, lab)
        c.setFont("Helvetica", 11); c.drawString(x + 5, y - 2, val)
    def usd(d): return f"{d:,.2f} USD"
    y = table(c, inv, 50, H - 290, [("Article", "description", 0, "l"), ("Units", "quantity", 300, "r"),
                                    ("Price/unit", "unit_price", 400, "r"), ("Value", "amount", 500, "r")], usd)
    totals_block(c, inv, 330, y - 20, usd, ("Net value", "Tax", "Grand total"))


def draw_statement(c, inv):
    """'Statement of charges' wording and YYYY/MM/DD dates. Test set only."""
    W, H = letter
    c.setFont("Times-Bold", 16); c.drawCentredString(W / 2, H - 55, inv["vendor_name"])
    c.setFont("Times-Roman", 9); c.drawCentredString(W / 2, H - 68, inv["vendor_address"])
    c.setFont("Times-Bold", 13); c.drawString(60, H - 110, "Statement of Charges")
    c.setFont("Times-Roman", 11)
    pay_by = (f"within {inv['terms_days']} days of statement date" if inv["terms_only"]
              else fmt_date(inv["due_date"], "slash_ymd"))
    for i, ln in enumerate([f"Account holder: {inv['bill_to']}", f"Statement ref: {inv['invoice_number']}",
                            f"Statement date: {fmt_date(inv['invoice_date'], 'slash_ymd')}",
                            f"Please pay by: {pay_by}", f"Customer account: {inv['customer_id']}"]):
        c.drawString(60, H - 132 - i * 15, ln)
    y = H - 230
    c.setFont("Times-Roman", 10)
    for it in inv["line_items"]:
        c.drawString(60, y, f"{it['quantity']} x {it['description']}  (at {money(it['unit_price']):,.2f} each)")
        c.drawRightString(W - 60, y, f"{money(it['amount']):,.2f}"); y -= 15
    c.line(60, y + 5, W - 60, y + 5)
    totals_block(c, inv, W - 250, y - 15, fmt_us, ("Charges", "Sales tax", "Amount payable"), font="Times-Roman")


DRAWERS = {"classic": draw_classic, "modern": draw_modern, "receipt": draw_receipt,
           "euro": draw_euro, "multipage": draw_multipage, "lettered": draw_lettered,
           "form": draw_form, "statement": draw_statement}


# ----------------------------------------------------------------- scanning
def scanify(pdf_bytes: bytes, rng: random.Random) -> Image.Image:
    page = pdfium.PdfDocument(pdf_bytes)[0]
    img = page.render(scale=150 / 72).to_pil().convert("L")
    img = img.rotate(rng.uniform(-2.5, 2.5), expand=True, fillcolor=245, resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.4, 0.9)))
    noise = Image.effect_noise(img.size, rng.uniform(18, 30))
    img = Image.blend(img, noise, 0.12)
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=rng.randint(45, 65))  # lossy, like a cheap scanner
    return Image.open(io.BytesIO(buf.getvalue())).convert("L")


def to_truth(inv: dict) -> dict:
    return {
        "vendor_name": inv["vendor_name"],
        "invoice_number": inv["invoice_number"],
        "invoice_date": inv["invoice_date"].isoformat(),
        "due_date": inv["due_date"].isoformat(),
        "bill_to": inv["bill_to"],
        "currency": inv["currency"],
        "subtotal": float(inv["subtotal"]),
        "tax": float(inv["tax"]),
        "total": float(inv["total"]),
        "line_items": inv["line_items"],
    }


def build(set_name: str):
    OUT_DIR, GT_PATH, seed, layout_counts = SETS[set_name]
    rng = random.Random(seed)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for f in OUT_DIR.iterdir():
        f.unlink()
    plan = [l for l, n in layout_counts.items() for _ in range(n)]
    rng.shuffle(plan)
    truth = {}
    for i, layout in enumerate(plan, start=1):
        inv = make_invoice(rng, i, layout)
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        DRAWERS[layout](c, inv)
        c.save()
        pdf_bytes = buf.getvalue()

        scanned = layout != "multipage" and rng.random() < 0.35
        stem = f"invoice_{i:02d}" if set_name == "practice" else f"test_{i:02d}"
        if scanned:
            img = scanify(pdf_bytes, rng)
            if rng.random() < 0.5:
                name, fmt = f"{stem}.png", "scanned image (PNG)"
                img.save(OUT_DIR / name)
            else:
                name, fmt = f"{stem}.pdf", "scanned PDF (image only, no text layer)"
                img.convert("RGB").save(OUT_DIR / name, "PDF", resolution=150)
        else:
            name, fmt = f"{stem}.pdf", "digital PDF"
            (OUT_DIR / name).write_bytes(pdf_bytes)

        truth[name] = {"_meta": {"layout": layout, "format": fmt, "due_date_from_terms_only": inv["terms_only"]},
                       **to_truth(inv)}
    GT_PATH.write_text(json.dumps(truth, indent=2))
    print(f"[{set_name}] wrote {len(truth)} invoices to {OUT_DIR} and answer key to {GT_PATH}")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["practice", "test", "both"], default="both")
    a = ap.parse_args()
    for name in (["practice", "test"] if a.set == "both" else [a.set]):
        build(name)


if __name__ == "__main__":
    main()
