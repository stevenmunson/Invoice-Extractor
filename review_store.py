"""Human review: which invoices go to an analyst, and what the analyst decided.

The review queue for a run is:
  - every invoice the AI flagged (needs_review) or that failed outright, and
  - a random "spot-check" sample of the invoices it did NOT flag.

The spot-check sample is how you measure silent errors in production, where there is no answer key:
if analysts have to correct some unflagged invoices, the AI is making mistakes it doesn't know about.

Decisions are saved per run in reviews/<run id>.json, so work survives restarts. Corrected
invoices are exactly the new answer-key data a real deployment accumulates over time.
"""
from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEWS_DIR = ROOT / "reviews"


def build_queue(run: dict, spot_check_pct: float) -> dict[str, list[str]]:
    flagged = [r["file"] for r in run["rows"] if r["fields"].get("needs_review") or r.get("error")]
    unflagged = [r["file"] for r in run["rows"] if r["file"] not in flagged]
    n = max(1, round(len(unflagged) * spot_check_pct / 100)) if unflagged and spot_check_pct > 0 else 0
    # Seeded by the run, so the same run always gives the same sample (no cherry-picking by re-rolling).
    sample = sorted(random.Random(run["run_at"]).sample(unflagged, min(n, len(unflagged))))
    return {"flagged": sorted(flagged), "spot_check": sample}


def _path(run_id: str) -> Path:
    return REVIEWS_DIR / f"{run_id}.json"


def load_reviews(run_id: str) -> dict:
    p = _path(run_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_review(run_id: str, file: str, queue: str, original: dict, final: dict, note: str = "") -> dict:
    changed = [k for k in final if k != "line_items" and _norm(original.get(k, "")) != _norm(final.get(k, ""))]
    if _items_key(original.get("line_items")) != _items_key(final.get("line_items")):
        changed.append("line_items")
    reviews = load_reviews(run_id)
    reviews[file] = {
        "status": "corrected" if changed else "approved",
        "queue": queue,
        "changed_fields": changed,
        "original": original,
        "final": final,
        "note": note,
        "reviewed_at": datetime.now().isoformat(timespec="seconds"),
    }
    REVIEWS_DIR.mkdir(exist_ok=True)
    _path(run_id).write_text(json.dumps(reviews, indent=2), encoding="utf-8")
    return reviews[file]


def clear_review(run_id: str, file: str) -> None:
    reviews = load_reviews(run_id)
    if reviews.pop(file, None) is not None:
        _path(run_id).write_text(json.dumps(reviews, indent=2), encoding="utf-8")


def _norm(v):
    """Compare 0 and 0.0, or 1234.5 and "1234.50", as equal."""
    try:
        return round(float(str(v).replace(",", "")), 2)
    except ValueError:
        return str(v if v is not None else "").strip()


def _items_key(items) -> list:
    out = []
    for it in items or []:
        try:
            out.append((str(it.get("description", "")).strip(), float(it.get("quantity") or 0),
                        round(float(it.get("unit_price") or 0), 2), round(float(it.get("amount") or 0), 2)))
        except (TypeError, ValueError):
            out.append(tuple(str(v) for v in it.values()))
    return out
