"""Model prices, used to turn token counts into dollars.

Prices are USD per 1 million tokens, from Anthropic's published model pricing
(checked October 2026). Prices change, so re-check before quoting numbers:
https://platform.claude.com/docs/en/models/overview
"""

PRICES = {
    # model id: (input $/MTok, output $/MTok)
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-opus-5-5": (4.00, 20.00),
}
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = PRICES.get(model, PRICES[DEFAULT_MODEL])
    return input_tokens / 1_000_000 * inp + output_tokens / 1_000_000 * out
