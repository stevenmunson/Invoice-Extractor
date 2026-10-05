"""
Command-line evaluation.

  python scripts/run_eval.py --method baseline
  python scripts/run_eval.py --method claude                       # uses ANTHROPIC_API_KEY
  python scripts/run_eval.py --method claude --model claude-haiku-4-5-20251001
  python scripts/run_eval.py --method claude --limit 3             # cheap smoke test
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.costs import DEFAULT_MODEL  # noqa: E402
from src.pipeline import run_batch  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", choices=["baseline", "claude"], default="baseline")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--set", choices=["practice", "test"], default="practice")
    a = ap.parse_args()

    key = os.environ.get("ANTHROPIC_API_KEY")
    if a.method == "claude" and not key:
        sys.exit("Set ANTHROPIC_API_KEY first (see README).")

    run = run_batch(a.method, api_key=key, model=a.model, limit=a.limit, dataset=a.set,
                    on_progress=lambda i, n, f: print(f"  [{i}/{n}] {f}", flush=True))
    s = run["summary"]
    print(f"\n[{a.set} set] {run['model']} (instructions: {run['prompt_version']}): {s['overall_accuracy']:.1%} field accuracy, "
          f"{s['perfect_documents']}/{s['documents']} documents perfect, {s['errors']} errors")
    for f, v in s["per_field"].items():
        print(f"  {f:<15} {v:6.1%}")
    print(f"Cost: ${s['total_cost_usd']:.4f} total, ${s['avg_cost_usd']:.4f}/invoice "
          f"(${s['avg_cost_usd'] * 1000:.2f} per 1,000), avg {s['avg_seconds']:.1f}s/invoice")


if __name__ == "__main__":
    main()
