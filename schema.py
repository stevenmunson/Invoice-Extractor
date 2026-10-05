"""The target data model: what we want out of every invoice, whatever it looks like."""

HEADER_FIELDS = [
    "vendor_name", "invoice_number", "invoice_date", "due_date", "bill_to",
    "currency", "subtotal", "tax", "total",
]
SCORED_FIELDS = HEADER_FIELDS + ["line_items"]
MONEY_FIELDS = {"subtotal", "tax", "total"}
DATE_FIELDS = {"invoice_date", "due_date"}

# JSON Schema handed to Claude via "structured outputs" (output_config.format).
# Claude's reply is then guaranteed to be valid JSON in exactly this shape.
# Rules for these schemas: every object needs "additionalProperties": False,
# and every property must be listed in "required".
INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor_name": {"type": "string", "description": "Company that issued the invoice (the seller)."},
        "invoice_number": {"type": "string", "description": "The seller's invoice/receipt/reference number. NOT a PO number or customer ID."},
        "invoice_date": {"type": "string", "description": "Issue date, ISO format YYYY-MM-DD."},
        "due_date": {"type": "string", "description": "Payment due date, YYYY-MM-DD. If only terms like 'Net 30' are given, compute it from the invoice date. Empty string if there is no way to tell."},
        "bill_to": {"type": "string", "description": "Customer being billed (bill-to / invoice recipient), not the ship-to party."},
        "currency": {"type": "string", "description": "ISO 4217 code, e.g. USD, EUR."},
        "subtotal": {"type": "number", "description": "Pre-discount, pre-tax sum of line items."},
        "tax": {"type": "number", "description": "Total tax / VAT amount. 0 if none."},
        "total": {"type": "number", "description": "Final amount payable."},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit_price": {"type": "number"},
                    "amount": {"type": "number"},
                },
                "required": ["description", "quantity", "unit_price", "amount"],
                "additionalProperties": False,
            },
        },
        "needs_review": {"type": "boolean", "description": "True if anything was unreadable, ambiguous, or the numbers don't add up."},
        "review_reason": {"type": "string", "description": "Short reason when needs_review is true, else empty."},
    },
    "required": ["vendor_name", "invoice_number", "invoice_date", "due_date", "bill_to",
                 "currency", "subtotal", "tax", "total", "line_items", "needs_review", "review_reason"],
    "additionalProperties": False,
}

EXTRACTION_PROMPT = """You are extracting data from a business invoice for an accounts-payable system.
Read the whole document (all pages) and return the invoice's data as JSON.

Rules:
- Dates: output YYYY-MM-DD. Watch for day-first European formats (e.g. 05.03.2026 is 5 March).
- Numbers: plain numbers with no currency symbols or thousands separators. European "1.234,56" is 1234.56.
- If there is a discount, subtotal is the amount BEFORE the discount.
- Don't confuse the invoice number with PO numbers, order references or customer IDs.
- If something is unreadable or the line items don't add up to the subtotal, set needs_review=true and say why.
"""
