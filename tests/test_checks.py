"""
Automatic checks that run on GitHub every time the project changes (see .github/workflows/checks.yml).
They are free and need no API key: they test the scoring logic, the answer keys, the schema rules,
and the rule-based reader. If any check fails, GitHub shows a red X instead of a green check.

Run locally (optional):  python tests/test_checks.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import extract_baseline  # noqa: E402
from src.evaluate import score, score_field  # noqa: E402
from src.pipeline import invoice_dir, load_truth  # noqa: E402
from src.schema import INVOICE_SCHEMA, SCORED_FIELDS  # noqa: E402


# --- Scoring: is the grading itself correct? ---------------------------------------------------
def test_money_matches_ignore_formatting():
    assert score_field("total", "1,234.50", 1234.5) == 1
    assert score_field("total", 1234.75, 1234.5) == 0  # (a 1-cent difference is allowed for rounding)


def test_dates_must_match_exactly():
    assert score_field("invoice_date", "2026-03-05", "2026-03-05") == 1
    assert score_field("invoice_date", "2026-05-03", "2026-03-05") == 0  # day/month swapped


def test_names_ignore_case_and_punctuation():
    assert score_field("vendor_name", "NORTHWIND OFFICE SUPPLY", "Northwind Office Supply") == 1
    assert score_field("bill_to", "Accounts Payable, Harborview Hotel Group", "Harborview Hotel Group") == 0


def test_perfect_answer_scores_100_percent():
    truth = next(iter(load_truth("practice").values()))
    answer = {k: v for k, v in truth.items() if k != "_meta"}
    assert score(answer, truth)["overall"] == 1.0


# --- Answer keys: are the test data intact? ----------------------------------------------------
def test_both_sets_have_30_invoices_with_files():
    for dataset in ("practice", "test"):
        truth = load_truth(dataset)
        assert len(truth) == 30, dataset
        for name, t in truth.items():
            assert (invoice_dir(dataset) / name).exists(), name
            assert all(f in t for f in SCORED_FIELDS), name


def test_answer_keys_add_up():
    """Line items should sum to the subtotal in every answer key (catches a corrupted key)."""
    for dataset in ("practice", "test"):
        for name, t in load_truth(dataset).items():
            total_items = round(sum(i["amount"] for i in t["line_items"]), 2)
            assert abs(total_items - t["subtotal"]) < 0.011, name


# --- Schema: will Claude's structured-outputs feature accept it? -------------------------------
def test_schema_follows_structured_output_rules():
    def check(node, path="schema"):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, f"{path} needs additionalProperties: False"
            assert set(node["required"]) == set(node["properties"]), f"{path}: every property must be required"
            for key, child in node["properties"].items():
                check(child, f"{path}.{key}")
        if node.get("type") == "array":
            check(node["items"], f"{path}[]")
        assert not isinstance(node.get("type"), list), f"{path}: use a single type"
    check(INVOICE_SCHEMA)
    json.dumps(INVOICE_SCHEMA)  # must be plain JSON


# --- Rule-based reader: a free "canary" for broken file handling -------------------------------
def test_rule_based_reader_still_works():
    truth = load_truth("practice")
    scores = []
    for name, t in truth.items():
        result = extract_baseline.extract_file(invoice_dir("practice") / name)
        scores.append(score(result["fields"], t)["overall"])
    accuracy = sum(scores) / len(scores)
    assert accuracy >= 0.40, f"rule-based accuracy fell to {accuracy:.0%} (normally 44%)"


# --- Evaluation gate: does the pull-request report judge changes correctly? --------------------
def _fake_run(broken=(), model="claude-sonnet-5-5"):
    truth = load_truth("practice")
    rows = []
    for name, t in sorted(truth.items()):
        fields = {k: v for k, v in t.items() if k != "_meta"}
        if name in broken:
            fields["total"] = 0.0
        rows.append({"file": name, "fields": fields, "scores": score(fields, t), "cost_usd": 0.01,
                     "seconds": 4.0, "error": None})
    return {"model": model, "dataset": "practice", "prompt": "same", "prompt_version": "v1",
            "schema_hash": "x", "rows": rows}


def test_eval_gate_passes_identical_runs_and_fails_regressions():
    sys.path.insert(0, str(ROOT / "scripts"))
    from eval_gate import build_report
    report, failed = build_report(_fake_run(), _fake_run(), flaky=[], before_cached=True)
    assert not failed and "✅ **Passed" in report
    report, failed = build_report(_fake_run(), _fake_run(broken=["invoice_01.pdf"]), flaky=[], before_cached=True)
    assert failed and "invoice_01.pdf (Total)" in report


if __name__ == "__main__":  # lets you run the checks without installing pytest
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} checks passed")
    sys.exit(1 if failed else 0)
