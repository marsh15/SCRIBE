"""Shared, dated provider prices and pure token-to-USD arithmetic.

This module is safe for both the retrieval service and the offline evaluation CLI to import.
It performs no provider calls and has no dependency on ``scribe_eval``.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICE_TABLE_DATE = "2026-10-21"

UNIT_INPUT = "usd_per_1M_input_tokens"
UNIT_OUTPUT = "usd_per_1M_output_tokens"
UNIT_TOKENS = "usd_per_1M_tokens"

GEMINI_PRICING = "https://ai.google.dev/gemini-api/docs/pricing"
VOYAGE_PRICING = "https://docs.voyageai.com/docs/pricing"


@dataclass(frozen=True)
class Price:
    model_id: str
    kind: str
    unit: str
    usd: float
    source_note: str


def _p(model: str, kind: str, unit: str, usd: float, note: str, source: str = GEMINI_PRICING) -> Price:
    return Price(model, kind, unit, usd, f"Verified {PRICE_TABLE_DATE}: {note} Source: {source}.")


PRICES: tuple[Price, ...] = (
    Price("gemini-embedding-001", "embedding", UNIT_TOKENS, 0.15,
          "UNVERIFIED: Gemini pricing page does not list embedding-001. Source checked 2026-10-21: "
          "https://ai.google.dev/gemini-api/docs/pricing."),
    _p("gemini-embedding-2", "embedding", UNIT_TOKENS, 0.20,
       "Gemini API paid tier, text embedding input, USD per 1M tokens."),
    _p("gemini-2.5-flash", "generation", UNIT_INPUT, 0.30,
       "Gemini API paid tier, text/image/video input, per 1M tokens."),
    _p("gemini-2.5-flash", "generation", UNIT_OUTPUT, 2.50,
       "Gemini API paid tier, output incl. thinking tokens, per 1M."),
    _p("gemini-2.5-pro", "generation", UNIT_INPUT, 1.25,
       "Gemini API paid tier, prompts <= 200k tokens, per 1M tokens."),
    _p("gemini-2.5-pro", "generation", UNIT_OUTPUT, 10.00,
       "Gemini API paid tier, prompts <= 200k tokens, per 1M tokens."),
    _p("gemini-2.5-flash-lite", "generation", UNIT_INPUT, 0.10,
       "Gemini API paid tier, text input, per 1M tokens."),
    _p("gemini-2.5-flash-lite", "generation", UNIT_OUTPUT, 0.40,
       "Gemini API paid tier, output, per 1M tokens."),
    _p("rerank-2.5", "rerank", UNIT_TOKENS, 0.05,
       "Voyage AI rerank-2.5, per 1M tokens (query tokens x documents + document tokens). Voyage says the first "
       "200M tokens are free, but the pricing page contradicts itself; no free-tier assumption is applied.",
       VOYAGE_PRICING),
)
_BY_KEY = {(price.model_id, price.unit): price for price in PRICES}


class UnknownPrice(KeyError):
    """Raised when a model/unit has no entry in the shared price table."""


def price_usd(model_id: str, unit: str) -> float:
    try:
        return _BY_KEY[(model_id, unit)].usd
    except KeyError:
        raise UnknownPrice(f"no price for model {model_id!r} unit {unit!r}; add it to scribe_intel.pricing.PRICES") from None


def is_fake_model(model_id: str) -> bool:
    return model_id.startswith("fake")


def tokens_usd(model_id: str, input_tokens: int = 0, output_tokens: int = 0) -> float:
    """USD for a call; single-rate models price the sum of input and output tokens."""
    if is_fake_model(model_id):
        return 0.0
    if (model_id, UNIT_TOKENS) in _BY_KEY:
        return (input_tokens + output_tokens) * price_usd(model_id, UNIT_TOKENS) / 1_000_000
    usd = input_tokens * price_usd(model_id, UNIT_INPUT) / 1_000_000
    if output_tokens:
        usd += output_tokens * price_usd(model_id, UNIT_OUTPUT) / 1_000_000
    return usd
