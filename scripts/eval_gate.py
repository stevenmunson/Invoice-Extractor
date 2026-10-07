"""
Evaluation gate (Stage 2 of CI): runs Claude on an invoice set and compares the result with the
version on `main`, so a pull request shows exactly how a change affects accuracy, flags and cost.
Used by .github/workflows/eval.yml; needs ANTHROPIC_API_KEY.

  python scripts/eval_gate.py run     --project main --set practice --out before.json
  python scripts/eval_gate.py run     --project .    --set practice --out after.json [--model M]
  python scripts/eval_gate.py compare --before before.json --after after.json --out report.md
  python scripts/eval_gate.py summary --after after.json --out report.md      (test set: headline only)

`compare` exits with code 1 when the change makes results worse (the gate "fails").
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Thresholds: worse than this is a warning (needs a deliberate approval), not a failure.
COST_WARN = 0.20      # cost per invoice up more than 20%
SPEED_WARN = 0.30     # seconds per invoice up more than 30%
ACCURACY_TOL = 0.005  # ignore accuracy wobbles smaller than half a percentage point

MODEL_SHORT = {"claude-sonnet-5-5": "Sonnet 5.5", "claude-haiku-4-5-20251001": "Haiku 4.5",
               "claude-opus-5-5": "Opus 5.5"}
FIELD_LABELS = {"vendor_name": "Vendor", "invoice_number": "Invoice #", "invoice_date": "Invoice date",
                "due_date": "Due date", "bill_to": "Bill to", "currency": "Currency", "subtotal": "Subtotal",
                "tax": "Tax", "total": "Total", "line_items": "Line items"}


# ----------------------------------------------------------------------------- run
def cmd_run(a):
    """Evaluate one copy of the project (e.g. the `main` checkout, or this branch)."""
    project = Path(a.project).resolve()
    sys.path.insert(0, str(project))  # use THAT copy's code, instructions and schema
    from src.costs import DEFAULT_MODEL
    from src.pipeline import run_batch
    from src.schema import INVOICE_SCHEMA

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        sys.exit("ANTHROPIC_API_KEY is not set. Add it under Settings > Secrets and variables > Actions.")
    model = a.model if a.model and a.model != "default" else DEFAULT_MODEL
    print(f"Evaluating {project.name or 'project'} on the {a.set} set with {model} ...", flush=True)
    run = run_batch("claude", api_key=key, model=model, dataset=a.set,
                    on_progress=lambda i, n, f: print(f"  [{i}/{n}] {f}", flush=True))
    run["schema_hash"] = hashlib.sha256(json.dumps(INVOICE_SCHEMA, sort_keys=True).encode()).hexdigest()[:10]
    Path(a.out).write_text(json.dumps(run, indent=2, default=str))
    print(f"Saved {a.out}: {run['summary']['overall_accuracy']:.1%} field accuracy")


# ----------------------------------------------------------------------------- metrics
def metrics(run: dict) -> dict:
    rows = run["rows"]
    n = len(rows) or 1
    fields = list(rows[0]["scores"]) if rows else []
    fields = [f for f in fields if f != "overall"]
    flagged = [r for r in rows if r["fields"].get("needs_review")]
    return {
        "accuracy": sum(r["scores"]["overall"] for r in rows) / n,
        "perfect": sum(r["scores"]["overall"] == 1 for r in rows),
        "documents": len(rows),
        "silent": sum(r["scores"]["overall"] < 1 and not r["fields"].get("needs_review") and not r.get("error")
                      for r in rows),
        "flagged": len(flagged),
        "false_alarms": sum(r["scores"]["overall"] == 1 for r in flagged),
        "cost": sum(r["cost_usd"] for r in rows) / n,
        "total_cost": sum(r["cost_usd"] for r in rows),
        "seconds": sum(r["seconds"] for r in rows) / n,
        "failed": sum(bool(r.get("error")) for r in rows),
        "per_field": {f: sum(r["scores"][f] for r in rows) / n for f in fields},
    }


def short(model: str) -> str:
    return MODEL_SHORT.get(model, model)


def describe_change(before: dict, after: dict) -> tuple[list[str], list[str], tuple[str, str]]:
    """What stayed the same, what changed, and the column labels."""
    same, changed, cols = [], [], []
    if before["model"] == after["model"]:
        same.append(short(after["model"]))
    else:
        changed.append(f"model {short(before['model'])} → {short(after['model'])}")
        cols.append((short(before["model"]), short(after["model"])))

    def ver(run):
        v = run.get("prompt_version") or "original"
        return "edited" if v == "unsaved" else v

    if (before.get("prompt") or "").strip() == (after.get("prompt") or "").strip():
        same.append(f"instructions {ver(after)}")
    else:
        changed.append(f"instructions {ver(before)} → {ver(after)}")
        cols.append((f"instructions {ver(before)}", f"instructions {ver(after)}"))
    if before.get("schema_hash") != after.get("schema_hash"):
        changed.append("schema changed")
        cols.append(("old schema", "new schema"))
    if not changed:
        changed.append("code change only")
    labels = cols[0] if len(cols) == 1 else ("main", "this branch")
    return same, changed, labels


# ----------------------------------------------------------------------------- compare
def retry_newly_broken(after: dict, names: list[str]) -> list[str]:
    """Re-run invoices that newly failed, once. Returns the ones that passed on retry (run-to-run noise).
    Their rows in `after` are replaced by the retry result."""
    if not names:
        return []
    sys.path.insert(0, str(ROOT))
    from src.evaluate import score
    from src.pipeline import invoice_dir, load_truth, run_one
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    truth = load_truth(after["dataset"])
    passed = []
    for r in after["rows"]:
        if r["file"] not in names:
            continue
        print(f"  Retrying {r['file']} ...", flush=True)
        try:
            res = run_one(invoice_dir(after["dataset"]) / r["file"], "claude", key, after["model"], after["prompt"])
        except Exception as e:  # still broken
            r["retry_cost"] = 0.0
            r["retry_error"] = str(e)
            continue
        r["retry_cost"] = res["cost_usd"]
        new_scores = score(res["fields"], truth[r["file"]])
        if new_scores["overall"] == 1:
            passed.append(r["file"])
            r.update({"fields": res["fields"], "scores": new_scores, "error": None, "retried": True})
    return passed


def build_report(before: dict, after: dict, flaky: list[str], before_cached: bool) -> tuple[str, bool]:
    b, a = metrics(before), metrics(after)
    same, changed, (lb, la) = describe_change(before, after)
    bscore = {r["file"]: r for r in before["rows"]}
    ascore = {r["file"]: r for r in after["rows"]}
    fixed = [f for f in ascore if f in bscore and bscore[f]["scores"]["overall"] < 1 <= ascore[f]["scores"]["overall"]]
    broken = [f for f in ascore if f in bscore and bscore[f]["scores"]["overall"] == 1 > ascore[f]["scores"]["overall"]]

    failures, warnings = [], []

    def pct(x):
        return f"{x:.0%}" if abs(x * 100 - round(x * 100)) < 0.05 else f"{x:.1%}"

    def row(label, bv, av, fmt, status):
        return f"| {label} | {fmt(bv)} | {fmt(av)} | {status} |"

    lines = []
    # accuracy
    if a["accuracy"] < b["accuracy"] - ACCURACY_TOL:
        failures.append("field accuracy went down")
        st = "❌"
    else:
        st = "✅"
    lines.append(row("Field accuracy", b["accuracy"], a["accuracy"], pct, st))
    st = "❌" if a["perfect"] < b["perfect"] else "✅"
    if st == "❌":
        failures.append("fewer perfect invoices")
    lines.append(row("Perfect invoices", b["perfect"], a["perfect"], lambda v: f"{v}/{a['documents']}", st))
    st = "❌" if a["silent"] > b["silent"] else "✅"
    if st == "❌":
        failures.append("more silent errors")
    lines.append(row("Silent errors", b["silent"], a["silent"], str, st))
    for label, k in (("Flagged for review", "flagged"), ("False alarms (flagged but correct)", "false_alarms")):
        st = "⚠️" if a[k] > b[k] else "✅"
        if st == "⚠️":
            warnings.append(f"{label.split(' (')[0].lower()} went up")
        lines.append(row(label, b[k], a[k], str, st))
    for label, k, limit, fmt in (("Cost per invoice", "cost", COST_WARN, lambda v: f"${v:.4f}"),
                                 ("Avg seconds per invoice", "seconds", SPEED_WARN, lambda v: f"{v:.1f}")):
        change = (a[k] - b[k]) / b[k] if b[k] else 0.0
        if change > limit:
            st = f"⚠️ +{change:.0%}"
            warnings.append(f"{label.lower()} up {change:.0%}")
        else:
            st = "✅"
        lines.append(row(label, b[k], a[k], fmt, st))
    st = "❌" if a["failed"] > b["failed"] else "✅"
    if st == "❌":
        failures.append("more failed requests")
    lines.append(row("Failed requests", b["failed"], a["failed"], str, st))
    if broken:
        failures.append("an invoice that used to be fully correct now has a mistake")

    def missed(f):
        return ", ".join(FIELD_LABELS.get(k, k) for k, v in ascore[f]["scores"].items() if k != "overall" and v < 1)

    movers = [f"{FIELD_LABELS.get(k, k)} {pct(b['per_field'].get(k, 0))} → {pct(v)}"
              for k, v in a["per_field"].items() if abs(v - b["per_field"].get(k, 0)) > 1e-9]
    retry_cost = sum(r.get("retry_cost", 0.0) for r in after["rows"])
    run_cost = a["total_cost"] + retry_cost + (0 if before_cached else b["total_cost"])

    if failures:
        verdict = "❌ **Failed:** " + "; ".join(failures) + "."
    elif warnings:
        verdict = ("⚠️ **Passed with warnings:** " + "; ".join(warnings)
                   + ". Fine if it's a deliberate tradeoff; approve consciously.")
    else:
        verdict = "✅ **Passed:** no regressions."

    title = f"Evaluation: {after['dataset']} set · " + " · ".join(same + changed)
    out = [f"### {title}", "", f"| | Before ({lb}) | After ({la}) | |", "|---|---|---|---|", *lines, "",
           f"**Changed invoices:** fixed: {', '.join(fixed) or 'none'} · newly broken: "
           + (", ".join(f"{f} ({missed(f)})" for f in broken) or "none")]
    if flaky:
        out.append(f"**Passed on retry:** {', '.join(flaky)} (missed on the first run, correct on a re-run; "
                   "likely run-to-run variation)")
    out += [f"**Fields that changed:** {'; '.join(movers) or 'none'}",
            f"**Run cost:** ${run_cost:.2f}" + (" (main's results reused from cache)" if before_cached else ""),
            "", verdict]
    return "\n".join(out) + "\n", bool(failures)


def cmd_compare(a):
    before = json.loads(Path(a.before).read_text())
    after = json.loads(Path(a.after).read_text())
    bmap = {r["file"]: r["scores"]["overall"] for r in before["rows"]}
    newly_broken = [r["file"] for r in after["rows"] if bmap.get(r["file"]) == 1 and r["scores"]["overall"] < 1]
    flaky = [] if a.no_retry else retry_newly_broken(after, newly_broken)
    report, failed = build_report(before, after, flaky, a.before_cached)
    Path(a.out).write_text(report)
    print(report)
    sys.exit(1 if failed else 0)


def cmd_summary(a):
    """Held-out test set: headline numbers only, so nobody tunes against individual test invoices."""
    after = json.loads(Path(a.after).read_text())
    m = metrics(after)
    ver = after.get("prompt_version") or "original"
    report = "\n".join([
        f"### Evaluation: {after['dataset']} set (held out) · {short(after['model'])} · instructions {ver}", "",
        "| | Result |", "|---|---|",
        f"| Field accuracy | {m['accuracy']:.1%} |",
        f"| Perfect invoices | {m['perfect']}/{m['documents']} |",
        f"| Silent errors | {m['silent']} |",
        f"| Flagged for review | {m['flagged']} |",
        f"| Cost per invoice | ${m['cost']:.4f} |",
        f"| Failed requests | {m['failed']} |", "",
        f"**Run cost:** ${m['total_cost']:.2f}", "",
        "_Headline numbers only: per-invoice results are hidden so the held-out set isn't tuned against._",
    ]) + "\n"
    Path(a.out).write_text(report)
    print(report)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--project", default=".")
    r.add_argument("--set", choices=["practice", "test"], default="practice")
    r.add_argument("--model", default="default")
    r.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--before", required=True)
    c.add_argument("--after", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--before-cached", action="store_true")
    c.add_argument("--no-retry", action="store_true")
    s = sub.add_parser("summary")
    s.add_argument("--after", required=True)
    s.add_argument("--out", required=True)
    a = ap.parse_args()
    {"run": cmd_run, "compare": cmd_compare, "summary": cmd_summary}[a.cmd](a)


if __name__ == "__main__":
    main()
