"""The AI's instructions, kept in a plain text file so they can be edited (from the app's Tune tab
or in Notepad) and versioned. Every saved version is logged, and every evaluation run records which
version it used, so you can see exactly which change moved the score."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .schema import EXTRACTION_PROMPT as DEFAULT_PROMPT

ROOT = Path(__file__).resolve().parents[1]
PROMPT_FILE = ROOT / "instructions.txt"
HISTORY_FILE = ROOT / "data" / "instruction_history.jsonl"


def load_prompt() -> str:
    if PROMPT_FILE.exists():
        text = PROMPT_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text + "\n"
    return DEFAULT_PROMPT


def _history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    return [json.loads(ln) for ln in HISTORY_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _same(a: str, b: str) -> bool:
    return a.strip() == b.strip()


def version_of(text: str) -> str:
    if _same(text, DEFAULT_PROMPT):
        return "default"
    for h in _history():
        if _same(h["text"], text):
            return h["version"]
    return "unsaved"


def save_prompt(text: str) -> str:
    """Save as the current instructions; returns its version label (v1, v2, ...)."""
    PROMPT_FILE.write_text(text.strip() + "\n", encoding="utf-8")
    existing = version_of(text)
    if existing not in ("unsaved",):
        return existing
    hist = _history()
    version = f"v{len(hist) + 1}"
    HISTORY_FILE.parent.mkdir(exist_ok=True)
    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"version": version, "saved_at": datetime.now().isoformat(timespec="seconds"),
                            "text": text.strip()}) + "\n")
    return version


def reset_prompt() -> None:
    if PROMPT_FILE.exists():
        PROMPT_FILE.unlink()


def history() -> list[dict]:
    return [{"version": "default", "saved_at": "", "text": DEFAULT_PROMPT.strip()}] + _history()
