"""Cost model and spend ledger for provider-calling eval commands (A4a/C2).

Nothing here talks to a provider. It holds:

* a DATED PRICE TABLE (``PRICES``). Never edit a price to make a budget fit.
* ``price_per_query``: embed + rerank + generation + judge tokens x price. Token counts come from provider responses
  when the caller has them; otherwise they are estimated with the repo's tiktoken proxy (``scribe_ingest.tokens``,
  vendored vocabulary, no download) and the result carries ``estimated: True``.
* an append-only spend ledger (``benchmarks/spend-ledger.jsonl``, git-ignored): one JSON line per provider-calling
  run ``{ts, run_id, kind, model_ids, tokens, usd, estimated, cached, cached_calls, live_calls}``.
  ``spend_total()`` sums ``usd``.
* the spend cap. ``CAP_USD`` ($10.00) is the ONLY place the number lives. ``--spend-cap`` may LOWER it, never raise
  it. ``preflight()`` refuses (callers exit 2) when ``ledger total + estimate > cap``. After the run the caller appends
  the actual spend with ``append_spend``.

Cached (replayed) calls cost $0: the ledger line records ``cached_calls`` and ``cached: true`` when every call of the
run was served from the disk cache.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

CAP_USD = 10.00
DEFAULT_LEDGER = Path("benchmarks/spend-ledger.jsonl")
PRICE_TABLE_DATE = "2026-10-21"

UNIT_INPUT = "usd_per_1M_input_tokens"
UNIT_OUTPUT = "usd_per_1M_output_tokens"
UNIT_TOKENS = "usd_per_1M_tokens"  # one price for all tokens (embedding input, rerank)

GEMINI_PRICING = "https://ai.google.dev/gemini-api/docs/pricing"
VOYAGE_PRICING = "https://docs.voyageai.com/docs/pricing"


@dataclass(frozen=True)
class Price:
    model_id: str
    kind: str  # embedding | generation | rerank
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
    _p("gemini-2.5-flash", "generation", UNIT_INPUT, 0.30, "Gemini API paid tier, text/image/video input, per 1M tokens."),
    _p("gemini-2.5-flash", "generation", UNIT_OUTPUT, 2.50, "Gemini API paid tier, output incl. thinking tokens, per 1M."),
    _p("gemini-2.5-pro", "generation", UNIT_INPUT, 1.25, "Gemini API paid tier, prompts <= 200k tokens, per 1M tokens."),
    _p("gemini-2.5-pro", "generation", UNIT_OUTPUT, 10.00, "Gemini API paid tier, prompts <= 200k tokens, per 1M tokens."),
    _p("gemini-2.5-flash-lite", "generation", UNIT_INPUT, 0.10, "Gemini API paid tier, text input, per 1M tokens."),
    _p("gemini-2.5-flash-lite", "generation", UNIT_OUTPUT, 0.40, "Gemini API paid tier, output, per 1M tokens."),
    _p("rerank-2.5", "rerank", UNIT_TOKENS, 0.05,
       "Voyage AI rerank-2.5, per 1M tokens (query tokens x documents + document tokens). Voyage says the first "
       "200M tokens are free, but the pricing page contradicts itself; no free-tier assumption is applied.", VOYAGE_PRICING),
)
_BY_KEY = {(p.model_id, p.unit): p for p in PRICES}


class SpendError(Exception):
    """A spend-cap refusal or a bad cap request. Commands print it and exit 2."""


class UnknownPrice(KeyError):
    pass


def price_usd(model_id: str, unit: str) -> float:
    try:
        return _BY_KEY[(model_id, unit)].usd
    except KeyError:
        raise UnknownPrice(f"no price for model {model_id!r} unit {unit!r}; add it to scribe_eval.cost.PRICES") from None


def is_fake_model(model_id: str) -> bool:
    return model_id.startswith("fake")


def tokens_usd(model_id: str, input_tokens: int = 0, output_tokens: int = 0) -> float:
    """USD for one call's tokens. Fake models are free. Models priced with a single per-token price use UNIT_TOKENS
    for all tokens; others use separate input/output prices."""
    if is_fake_model(model_id):
        return 0.0
    if (model_id, UNIT_TOKENS) in _BY_KEY:
        return (input_tokens + output_tokens) * price_usd(model_id, UNIT_TOKENS) / 1_000_000
    usd = input_tokens * price_usd(model_id, UNIT_INPUT) / 1_000_000
    if output_tokens:
        usd += output_tokens * price_usd(model_id, UNIT_OUTPUT) / 1_000_000
    return usd


# ---------------------------------------------------------------- token counts

def estimate_tokens(text: str) -> int:
    """tiktoken (o200k_base proxy, vocabulary vendored in the repo) or, if tiktoken cannot load, ceil(chars / 4)."""
    if not text:
        return 0
    try:
        from scribe_ingest.tokens import count_tokens

        return int(count_tokens(text))
    except Exception:  # noqa: BLE001 - any tiktoken problem falls back, the result is labelled estimated anyway
        return math.ceil(len(text) / 4)


@dataclass(frozen=True)
class Usage:
    """Tokens one provider call used. ``estimated`` is True unless both counts came from the provider's response."""

    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated: bool = True
    cached: bool = False  # served from the replay cache: costs $0

    @property
    def usd(self) -> float:
        return 0.0 if self.cached else tokens_usd(self.model_id, self.input_tokens, self.output_tokens)

    @property
    def list_usd(self) -> float:
        """What the call costs when it is NOT cached (budgeting)."""
        return tokens_usd(self.model_id, self.input_tokens, self.output_tokens)


def usage_from_provider(model_id: str, input_tokens: int | None, output_tokens: int | None = None, *,
                        fallback_input_text: str = "", fallback_output_text: str = "",
                        cached: bool = False) -> Usage:
    """Provider-reported counts when present, else tiktoken estimates (estimated=True if ANY count was estimated)."""
    est = False
    if input_tokens is None:
        input_tokens, est = estimate_tokens(fallback_input_text), True
    if output_tokens is None:
        output_tokens = estimate_tokens(fallback_output_text) if fallback_output_text else 0
        est = est or bool(fallback_output_text)
    return Usage(model_id, int(input_tokens), int(output_tokens), est, cached)


@dataclass
class QueryCost:
    usd: float
    list_usd: float
    estimated: bool
    cached: bool
    tokens: dict[str, int]
    model_ids: dict[str, str]
    parts_usd: dict[str, float]

    def as_json(self) -> dict[str, Any]:
        return {"usd": round(self.usd, 8), "list_usd": round(self.list_usd, 8), "estimated": self.estimated,
                "cached": self.cached, "tokens": self.tokens, "model_ids": self.model_ids,
                "parts_usd": {k: round(v, 8) for k, v in self.parts_usd.items()}}


def price_per_query(*, embed: Usage | None = None, rerank: Usage | None = None, generation: Usage | None = None,
                    judge: Usage | None = None) -> QueryCost:
    """Price per query = embed + rerank + generation + judge tokens x price. ``usd`` counts only live calls (cached
    parts cost $0); ``list_usd`` is the uncached price."""
    parts = {k: u for k, u in (("embed", embed), ("rerank", rerank), ("generation", generation), ("judge", judge))
             if u is not None}
    tokens: dict[str, int] = {}
    for name, u in parts.items():
        tokens[f"{name}_input"] = u.input_tokens
        if u.output_tokens:
            tokens[f"{name}_output"] = u.output_tokens
    return QueryCost(
        usd=sum(u.usd for u in parts.values()), list_usd=sum(u.list_usd for u in parts.values()),
        estimated=any(u.estimated for u in parts.values()),
        cached=bool(parts) and all(u.cached for u in parts.values()), tokens=tokens,
        model_ids={n: u.model_id for n, u in parts.items()}, parts_usd={n: u.usd for n, u in parts.items()})


def cost_summary(costs: Iterable[dict[str, Any] | None]) -> dict[str, Any]:
    """Aggregate per-case ``QueryCost.as_json()`` dicts (rows without a usd are ignored)."""
    rows = [c for c in costs if c and "usd" in c]
    usd = sum(c["usd"] for c in rows)
    return {"queries": len(rows), "usd": round(usd, 8), "usd_per_query": round(usd / len(rows), 8) if rows else None,
            "list_usd": round(sum(c.get("list_usd", c["usd"]) for c in rows), 8),
            "estimated": any(c.get("estimated") for c in rows),
            "cached_queries": sum(1 for c in rows if c.get("cached"))}


# ---------------------------------------------------------------- ledger

def _ledger(path: str | Path | None) -> Path:
    return Path(path) if path else DEFAULT_LEDGER


def read_ledger(path: str | Path | None = None) -> list[dict[str, Any]]:
    p = _ledger(path)
    if not p.is_file():
        return []
    out = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            float(rec["usd"])
        except (ValueError, KeyError, TypeError) as exc:
            # never undercount silently: a damaged ledger blocks spending until a person looks at it
            raise SpendError(f"{p}:{n}: unreadable spend-ledger line ({type(exc).__name__}); fix it before spending") from None
        out.append(rec)
    return out


def spend_total(path: str | Path | None = None) -> float:
    return sum(float(r["usd"]) for r in read_ledger(path))


def append_spend(*, run_id: str, kind: str, model_ids: Sequence[str] | dict[str, str], tokens: dict[str, int],
                 usd: float, estimated: bool, cached_calls: int = 0, live_calls: int = 0,
                 path: str | Path | None = None, ts: str | None = None) -> dict[str, Any]:
    """Append ONE line. ``cached`` is true when there were calls and all were replayed; such a line has usd 0."""
    cached = cached_calls > 0 and live_calls == 0
    rec = {"ts": ts or datetime.now(timezone.utc).isoformat(), "run_id": run_id, "kind": kind,
           "model_ids": dict(model_ids) if isinstance(model_ids, dict) else sorted(set(model_ids)),
           "tokens": tokens, "usd": 0.0 if cached else round(float(usd), 8), "estimated": bool(estimated),
           "cached": cached, "cached_calls": cached_calls, "live_calls": live_calls}
    p = _ledger(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:  # append-only; O_APPEND keeps concurrent lines whole
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return rec


def resolve_cap(requested: float | None) -> float:
    """The cap in force. ``--spend-cap`` can only lower it; raising above CAP_USD (or a negative cap) is rejected."""
    if requested is None:
        return CAP_USD
    if not math.isfinite(requested) or requested < 0:
        raise SpendError(f"--spend-cap must be a finite number >= 0, got {requested}")
    if requested > CAP_USD:
        raise SpendError(f"--spend-cap {requested:.2f} would raise the cap above the ${CAP_USD:.2f} limit; it can "
                         "only be lowered")
    return float(requested)


def preflight(estimate_usd: float, *, spend_cap: float | None = None, path: str | Path | None = None,
              what: str = "run") -> tuple[float, float]:
    """Refuse (SpendError) when ledger total + estimate > cap. Returns (ledger_total, cap) when allowed."""
    cap = resolve_cap(spend_cap)
    total = spend_total(path)
    if total + estimate_usd > cap + 1e-12:
        raise SpendError(
            f"refusing to {what}: ledger total ${total:.4f} + pre-run estimate ${estimate_usd:.4f} = "
            f"${total + estimate_usd:.4f} exceeds the ${cap:.2f} spend cap ({_ledger(path)}). Nothing was called.")
    return total, cap


# ---------------------------------------------------------------- metering wrappers (real providers only)

class MeteredEmbedder:
    """Wraps an Embedder and counts tokens + live calls + real wall time of every embed call.

    ``live`` is detected from the inner embedder's ``texts_embedded`` counter (GeminiEmbedder increments it only for
    provider-embedded texts, i.e. cache misses). Embedders without it count every text as live (conservative)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.texts = 0
        self.live_texts = 0
        self.tokens = 0
        self.live_tokens = 0
        self.last_ms = 0.0
        self.last_live = False
        self.total_ms = 0.0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def _meter(self, fn: Any, texts: Sequence[str], *args: Any) -> list[list[float]]:
        before = getattr(self.inner, "texts_embedded", None)
        t0 = time.monotonic()
        out = await fn(texts, *args)
        self.last_ms = (time.monotonic() - t0) * 1000
        self.total_ms += self.last_ms
        n = len(texts)
        tok = sum(estimate_tokens(t) for t in texts)
        live = n if before is None else min(n, getattr(self.inner, "texts_embedded") - before)
        self.texts += n
        self.live_texts += live
        self.tokens += tok
        self.live_tokens += round(tok * live / n) if n else 0
        self.last_live = live > 0
        return out

    async def embed_documents(self, texts: Sequence[str], titles: Sequence[str | None] | None = None) -> list[list[float]]:
        return await self._meter(self.inner.embed_documents, texts, titles)

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._meter(self.inner.embed_queries, texts)

    def usage(self) -> Usage:
        return Usage(self.inner.model, self.live_tokens, 0, True, cached=self.live_texts == 0 and self.texts > 0)


@dataclass
class RerankMeter:
    tokens: int = 0
    live_tokens: int = 0
    calls: int = 0
    live_calls: int = 0
    model: str = "rerank-2.5"


class MeteredReranker:
    """Wraps a (cached) reranker. ``cache`` is the DiskCache whose ``misses`` counter tells live from replayed."""

    def __init__(self, inner: Any, cache: Any, meter: RerankMeter) -> None:
        self.inner, self.cache, self.meter = inner, cache, meter
        self.model = inner.model
        self.is_fake = inner.is_fake
        meter.model = inner.model

    async def rerank(self, query: str, docs: list[str], top_n: int) -> Any:
        before = getattr(self.cache, "misses", 0)
        out = await self.inner.rerank(query, docs, top_n)
        # Voyage bills query tokens x documents + document tokens (reranker.py docstring: the 600K-per-request rule)
        tok = estimate_tokens(query) * len(docs) + sum(estimate_tokens(d) for d in docs)
        live = getattr(self.cache, "misses", 0) > before
        self.meter.tokens += tok
        self.meter.calls += 1
        if live:
            self.meter.live_tokens += tok
            self.meter.live_calls += 1
        return out


def rerank_usage(meter: RerankMeter) -> Usage | None:
    if not meter.calls:
        return None
    return Usage(meter.model, meter.live_tokens, 0, True, cached=meter.live_calls == 0)


@dataclass
class RunSpend:
    """Accumulates what a run spent, then writes the one ledger line."""

    kind: str
    run_id: str
    usages: list[Usage] = field(default_factory=list)
    cached_calls: int = 0
    live_calls: int = 0

    def add(self, usage: Usage | None, *, calls: int = 1) -> None:
        if usage is None:
            return
        self.usages.append(usage)
        if usage.cached:
            self.cached_calls += calls
        else:
            self.live_calls += calls

    def write(self, path: str | Path | None = None) -> dict[str, Any]:
        tokens: dict[str, int] = {}
        for u in self.usages:
            tokens[f"{u.model_id}:input"] = tokens.get(f"{u.model_id}:input", 0) + u.input_tokens
            if u.output_tokens:
                tokens[f"{u.model_id}:output"] = tokens.get(f"{u.model_id}:output", 0) + u.output_tokens
        return append_spend(
            run_id=self.run_id, kind=self.kind, model_ids=[u.model_id for u in self.usages], tokens=tokens,
            usd=sum(u.usd for u in self.usages), estimated=any(u.estimated for u in self.usages),
            cached_calls=self.cached_calls, live_calls=self.live_calls, path=path)


class MeteredOcr:
    """Wraps an OcrClient; sums the provider-reported token counts of every page (exact, not estimated)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.pages = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def transcribe(self, png: bytes, *, page: int) -> Any:
        res = await self.inner.transcribe(png, page=page)
        self.pages += 1
        self.input_tokens += int(getattr(res, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(res, "output_tokens", 0) or 0)
        return res

    def usage(self) -> Usage | None:
        if not self.pages:
            return None
        return Usage(self.inner.model, self.input_tokens, self.output_tokens, estimated=False)


GENERATION_OUTPUT_TOKENS_EST = 400  # pre-run budget assumption for one generated answer
VERIFY_OUTPUT_TOKENS_PER_CLAIM_EST = 20
CLAIMS_PER_ANSWER_EST = 5
VERIFY_OVERHEAD_TOKENS_EST = 300  # system prompt + schema around the quotes


class MeteredGenerator:
    """Wraps an answer Generator; the provider response carries no usage here, so tokens are tiktoken estimates."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.model_id, self.is_fake = inner.model_id, inner.is_fake
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0
        self.events: list[Usage] = []

    def generate(self, system: str, question: str, context: str) -> str:
        self.calls += 1
        in_tokens = estimate_tokens(system) + estimate_tokens(question) + estimate_tokens(context)
        try:
            out = self.inner.generate(system, question, context)
        except Exception:
            self.input_tokens += in_tokens
            self.events.append(Usage(self.model_id, in_tokens, 0, True))
            raise
        out_tokens = estimate_tokens(out)
        self.input_tokens += in_tokens
        self.output_tokens += out_tokens
        cached = bool(getattr(self.inner, "last_cached", False))
        self.events.append(Usage(self.model_id, in_tokens, out_tokens, True, cached=cached))
        return out

    @property
    def configured(self) -> bool:
        return bool(getattr(self.inner, "configured", True))

    def usage(self) -> Usage | None:
        return Usage(self.model_id, self.input_tokens, self.output_tokens, True) if self.calls else None


class MeteredVerifier:
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.model, self.is_fake = inner.model, inner.is_fake
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0
        self.events: list[Usage] = []

    def verify(self, claims: Sequence[tuple[int, str, list[str]]], quotes: dict[str, str]) -> dict[int, str]:
        in_tokens = VERIFY_OVERHEAD_TOKENS_EST + sum(estimate_tokens(t) for _, t, _ in claims) + sum(estimate_tokens(q) for q in quotes.values())
        out_tokens = VERIFY_OUTPUT_TOKENS_PER_CLAIM_EST * len(claims)
        self.calls += 1
        self.input_tokens += in_tokens
        self.output_tokens += out_tokens
        try:
            result = self.inner.verify(claims, quotes)
        except Exception:
            self.events.append(Usage(self.model, in_tokens, out_tokens, True))
            raise
        self.events.append(Usage(self.model, in_tokens, out_tokens, True,
                                 cached=bool(getattr(self.inner, "last_cached", False))))
        return result

    @property
    def configured(self) -> bool:
        return bool(getattr(self.inner, "configured", True))

    def usage(self) -> Usage | None:
        return Usage(self.model, self.input_tokens, self.output_tokens, True) if self.calls else None


class MeteredJudge:
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.model_id, self.is_fake = inner.model_id, inner.is_fake
        self.input_tokens = self.output_tokens = self.calls = 0
        self.events: list[Usage] = []

    def judge(self, question: str, reference: str, answer: str) -> str:
        prompt = f"{question}\n{reference}\n{answer}"
        in_tokens = estimate_tokens(prompt) + 300
        self.calls += 1
        self.input_tokens += in_tokens
        try:
            verdict = self.inner.judge(question, reference, answer)
        except Exception:
            self.events.append(Usage(self.model_id, in_tokens, 8, True))
            raise
        self.output_tokens += 8
        self.events.append(Usage(self.model_id, in_tokens, 8, True,
                                 cached=bool(getattr(self.inner, "last_cached", False))))
        return verdict

    @property
    def configured(self) -> bool:
        return bool(getattr(self.inner, "configured", True))

    def usage(self) -> Usage | None:
        return Usage(self.model_id, self.input_tokens, self.output_tokens, True) if self.calls else None
