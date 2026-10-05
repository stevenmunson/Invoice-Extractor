"""Score an extraction against the answer key, field by field."""
from __future__ import annotations

import re

from .schema import DATE_FIELDS, MONEY_FIELDS, SCORED_FIELDS


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").casefold()).strip()


def _num(x):
    try:
        return float(str(x).replace(",", "").replace("$", "")) if x not in (None, "") else None
    except ValueError:
        return None


def _line_items_score(pred, truth) -> float:
    """Share of true line items matched (same quantity and amount); extra rows are penalized."""
    pred = pred or []
    if not truth:
        return 1.0 if not pred else 0.0
    remaining = list(pred)
    matched = 0
    for t in truth:
        for p in remaining:
            if _num(p.get("amount")) is not None and abs(_num(p.get("amount")) - t["amount"]) < 0.011 \
                    and _num(p.get("quantity")) == t["quantity"]:
                matched += 1
                remaining.remove(p)
                break
    return matched / max(len(truth), len(pred))


def score_field(field: str, pred, truth) -> float:
    if field == "line_items":
        return _line_items_score(pred, truth)
    if field in MONEY_FIELDS:
        p = _num(pred)
        return float(p is not None and abs(p - truth) < 0.011)
    if field in DATE_FIELDS:
        return float(str(pred or "")[:10] == truth)
    if field == "currency":
        return float(str(pred or "").upper().strip() == truth)
    return float(_norm(pred) == _norm(truth))


def score(pred_fields: dict, truth: dict) -> dict:
    """Returns {field: score 0..1} plus 'overall' (mean of fields)."""
    scores = {f: score_field(f, (pred_fields or {}).get(f), truth[f]) for f in SCORED_FIELDS}
    scores["overall"] = sum(scores.values()) / len(SCORED_FIELDS)
    return scores
