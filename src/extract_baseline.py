"""Rule-based baseline: the "traditional" way, using text extraction + regular expressions.

This is intentionally the kind of thing a team might hand-build before AI:
it works on the layouts it was written for and breaks on everything else.
It can't read scanned images at all (no OCR) and doesn't attempt line items.
Comparing it to the AI extractor is the point.
"""
from __future__ import annotations

import io
import re
import time
from datetime import datetime
from pathlib import Path

DATE_FORMATS = ["%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d.%m.%Y"]
MONEY = r"\$?\s?([\d,]+\.\d{2})"


def _pdf_text(data: bytes) -> str:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def _date(s: str | None) -> str | None:
    if not s:
        return None
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s.strip(), f).date().isoformat()
        except ValueError:
            pass
    return None


def _find(pattern: str, text: str, flags=re.I) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _num(s: str | None) -> float | None:
    return float(s.replace(",", "")) if s else None


def _split_two_names(line: str) -> str:
    """Best guess at the first of two company names printed side by side."""
    m = re.match(r"(.+?(?:Inc\.|Co\.|LLC|Group|Partners|District|Union|Dental|Construction|Manufacturing))\s", line)
    return m.group(1) if m else line


def extract_text(text: str) -> dict:
    date_pat = r"([A-Z][a-z]+ \d{1,2}, \d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    vendor = next((ln for ln in lines if ln.upper() not in ("INVOICE", "RECEIPT")), None)
    inv_date = _date(_find(r"(?:Invoice\s+)?(?:Date|Issued)\s*:?\s*\n?\s*" + date_pat, text))
    due = _date(_find(r"Due\s*Date\s*:?\s*\n?\s*" + date_pat, text))
    net = _find(r"Net\s+(\d{1,3})", text)
    if not due and net and inv_date:  # "Net 30" -> invoice date + 30 days
        from datetime import date, timedelta
        due = (date.fromisoformat(inv_date) + timedelta(days=int(net))).isoformat()
    bill_to = _find(r"(?:Invoice to|Acct:|Sold to:|recipient:)\s*\n?\s*([A-Z][\w .&,'-]+?)(?:\s{2,}|\n|Ship|$)", text)
    if not bill_to:  # classic two-column "BILL TO / SHIP TO" header: take the left half of the next line
        m = re.search(r"BILL TO\s+SHIP TO\n(.+)", text)
        bill_to = _split_two_names(m.group(1)) if m else None
    return {
        "vendor_name": vendor,
        "invoice_number": _find(r"(?:Invoice\s*#|Invoice\s*No\.?|Receipt\s*No\.?)\s*:?\s*([A-Z0-9][A-Z0-9-]{3,})", text),
        "invoice_date": inv_date,
        "due_date": due,
        "bill_to": bill_to,
        "currency": "EUR" if re.search(r"\bEUR\b|€", text) else "USD",
        "subtotal": _num(_find(r"Subtotal\s*:?\s*" + MONEY, text)),
        "tax": _num(_find(r"(?:Sales\s+)?Tax(?:\s*\([\d.]+%\))?\s*:?\s*" + MONEY, text)) or 0.0,
        "total": _num(_find(r"(?:TOTAL DUE|Balance Due|AMOUNT DUE|Invoice Total)\s*:?\s*" + MONEY, text)),
        "line_items": [],
    }


def extract(data: bytes, suffix: str, **_) -> dict:
    start = time.perf_counter()
    text = _pdf_text(data) if suffix.lower() == ".pdf" else ""
    if not text.strip():
        fields, note = {}, "No text layer (scanned image) - rule-based approach can't read it"
    else:
        fields, note = extract_text(text), ""
    return {"fields": fields, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
            "seconds": time.perf_counter() - start, "model": "rule-based", "note": note}


def extract_file(path: str | Path, **_) -> dict:
    path = Path(path)
    return extract(path.read_bytes(), path.suffix)
