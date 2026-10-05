"""AI extraction: send the invoice (PDF or image) to Claude and get structured JSON back."""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from .costs import DEFAULT_MODEL, cost_usd
from .prompt_store import load_prompt
from .schema import INVOICE_SCHEMA

MEDIA_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _file_block(data: bytes, suffix: str) -> dict:
    media_type = MEDIA_TYPES.get(suffix.lower())
    if media_type is None:
        raise ValueError(f"Unsupported file type: {suffix}")
    source = {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(data).decode()}
    return {"type": "document" if media_type == "application/pdf" else "image", "source": source}


def extract(data: bytes, suffix: str, api_key: str, model: str = DEFAULT_MODEL, prompt: str | None = None) -> dict:
    """Returns {"fields": {...}, "input_tokens", "output_tokens", "cost_usd", "seconds", "model"}."""
    import anthropic  # imported here so the rest of the project works without it

    prompt = prompt or load_prompt()  # the current instructions (editable in the app's Tune tab)

    client = anthropic.Anthropic(api_key=api_key, max_retries=3)
    start = time.perf_counter()
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": [_file_block(data, suffix), {"type": "text", "text": prompt}]}],
        # Structured outputs: forces the reply to be JSON matching INVOICE_SCHEMA.
        # Sent via extra_body so it works with older and newer versions of the anthropic package.
        extra_body={"output_config": {"format": {"type": "json_schema", "schema": INVOICE_SCHEMA}}},
    )
    seconds = time.perf_counter() - start
    text = next((b.text for b in resp.content if b.type == "text"), "")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Claude's answer was cut off (hit max_tokens) - try again or raise max_tokens.")
    fields = json.loads(text) if text else {}
    it, ot = resp.usage.input_tokens, resp.usage.output_tokens
    return {"fields": fields, "input_tokens": it, "output_tokens": ot,
            "cost_usd": cost_usd(model, it, ot), "seconds": seconds, "model": model}


def extract_file(path: str | Path, api_key: str, model: str = DEFAULT_MODEL, prompt: str | None = None) -> dict:
    path = Path(path)
    return extract(path.read_bytes(), path.suffix, api_key, model, prompt)
