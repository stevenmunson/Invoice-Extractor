"""Run an extractor over a set of invoices, score it, and save the results.

Two sets: "practice" (look at it, tune on it as much as you like) and "test" (held out:
score it once at the end, never tune on it)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import extract_baseline, extract_claude
from .evaluate import score
from .prompt_store import load_prompt, version_of
from .schema import SCORED_FIELDS

ROOT = Path(__file__).resolve().parents[1]
DATASETS = {
    "practice": (ROOT / "data" / "invoices", ROOT / "data" / "ground_truth.json"),
    "test": (ROOT / "data" / "test" / "invoices", ROOT / "data" / "test" / "ground_truth.json"),
}
INVOICE_DIR = DATASETS["practice"][0]
RESULTS_DIR = ROOT / "results"


def invoice_dir(dataset: str = "practice") -> Path:
    return DATASETS[dataset][0]


def load_truth(dataset: str = "practice") -> dict:
    return json.loads(DATASETS[dataset][1].read_text())


def run_one(path: Path, method: str, api_key: str | None = None, model: str | None = None,
            prompt: str | None = None) -> dict:
    if method == "baseline":
        return extract_baseline.extract_file(path)
    return extract_claude.extract_file(path, api_key=api_key, model=model, prompt=prompt)


def run_batch(method: str, api_key: str | None = None, model: str | None = None,
              limit: int | None = None, on_progress=None, dataset: str = "practice") -> dict:
    truth = load_truth(dataset)
    names = sorted(truth)[:limit] if limit else sorted(truth)
    prompt = load_prompt() if method == "claude" else None
    rows = []
    for i, name in enumerate(names, start=1):
        try:
            res = run_one(invoice_dir(dataset) / name, method, api_key, model, prompt)
            error = None
        except Exception as e:  # keep going; one bad document shouldn't kill the batch
            res, error = {"fields": {}, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "seconds": 0.0}, str(e)
        rows.append({"file": name, **truth[name]["_meta"], **res, "error": error,
                     "scores": score(res["fields"], truth[name])})
        if on_progress:
            on_progress(i, len(names), name)
    label = "rule-based" if method == "baseline" else model
    version = version_of(prompt) if prompt else "n/a"
    run = {"method": method, "model": label, "dataset": dataset, "prompt_version": version, "prompt": prompt,
           "run_at": datetime.now().isoformat(timespec="seconds"), "summary": summarize(rows), "rows": rows}
    RESULTS_DIR.mkdir(exist_ok=True)
    # One file per set + method + model + instructions version, so tuning runs don't overwrite each other.
    name = f"{method}__{label}" + (f"__{version}" if method == "claude" else "")
    if dataset != "practice":
        name = f"{dataset}__{name}"
    (RESULTS_DIR / f"{name}.json").write_text(json.dumps(run, indent=2, default=str))
    return run


def summarize(rows: list[dict]) -> dict:
    n = len(rows) or 1
    per_field = {f: sum(r["scores"][f] for r in rows) / n for f in SCORED_FIELDS}
    total_cost = sum(r["cost_usd"] for r in rows)
    return {
        "documents": len(rows),
        "overall_accuracy": sum(r["scores"]["overall"] for r in rows) / n,
        "perfect_documents": sum(r["scores"]["overall"] == 1.0 for r in rows),
        "per_field": per_field,
        "flagged_for_review": sum(bool(r["fields"].get("needs_review")) for r in rows),
        "errors": sum(bool(r["error"]) for r in rows),
        "total_cost_usd": total_cost,
        "avg_cost_usd": total_cost / n,
        "max_cost_usd": max((r["cost_usd"] for r in rows), default=0),
        "avg_seconds": sum(r["seconds"] for r in rows) / n,
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
    }


def load_runs(dataset: str | None = None) -> list[dict]:
    if not RESULTS_DIR.exists():
        return []
    runs = []
    for p in RESULTS_DIR.glob("*.json"):
        r = json.loads(p.read_text())
        r["_id"] = p.stem  # used to file human reviews against this exact run
        r.setdefault("dataset", "practice")  # runs saved before the test set existed
        r.setdefault("prompt_version", "original" if r["method"] == "claude" else "n/a")
        if dataset is None or r["dataset"] == dataset:
            runs.append(r)
    return sorted(runs, key=lambda r: r["run_at"])
