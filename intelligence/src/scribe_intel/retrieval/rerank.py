"""Reranker adapter (DESIGN B4 step 6, ADR-0006, D13, D18, INV-16).

Voyage facts encoded here (docs.voyageai.com reranker + API reference, checked for T14):
  * POST https://api.voyageai.com/v1/rerank, Authorization: Bearer <key>
  * body: query (str), documents (list[str], max 1000), model, top_k (int|null, default all),
    return_documents (bool, default false), truncation (bool, default true)
  * response: {object:"list", data:[{index, relevance_score}] sorted by score desc,
    model, usage:{total_tokens}}
  * rerank-2.5: query <= 8,000 tokens, query+one document <= 32,000 tokens, and
    query_tokens*n_docs + sum(doc tokens) <= 600K per request.
Our call sites send <= 20 docs of a few hundred tokens, far inside these limits; docs are
still capped by `max_doc_tokens` and `truncation=true` is sent as a second safety net.

The API key travels only in the Authorization header; it never appears in URLs, errors, logs.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import random
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Protocol, Sequence

import httpx

from .types import RankedCandidate

DEFAULT_MODEL = "rerank-2.5"
ENDPOINT = "https://api.voyageai.com/v1/rerank"
MAX_DOCUMENTS = 1000
MAX_RETRIES = 4
DEFAULT_MAX_DOC_TOKENS = 2048
DEGRADED_RERANK = "rerank_unavailable"

ErrorCode = Literal["RATE_LIMITED", "TRANSIENT_PROVIDER", "AUTH", "INVALID_REQUEST"]


class RerankError(Exception):
    def __init__(self, code: ErrorCode, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.retryable = retryable

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.code}: {self.message}"


try:  # share the eval cache's exception when scribe_eval is importable
    from scribe_eval.cache import CacheMiss  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001  pragma: no cover
    class CacheMiss(RuntimeError):  # type: ignore[no-redef]
        """Equivalent of scribe_eval.cache.CacheMiss (scribe_eval unavailable)."""


@dataclass(frozen=True)
class RerankResult:
    index: int
    score: float


class Reranker(Protocol):
    is_fake: bool
    model: str

    async def rerank(self, query: str, docs: list[str], top_n: int) -> list[RerankResult]: ...


def doc_hash(doc: str) -> str:
    return hashlib.sha256(doc.encode("utf-8")).hexdigest()


def rerank_cache_key(model: str, query: str, docs: Sequence[str]) -> str:
    """D18. Identical to scribe_eval.cache.rerank_key(model, query, [sha256(doc)...])."""
    payload = json.dumps([model, query, [doc_hash(d) for d in docs]], ensure_ascii=False, separators=(",", ":"))
    return "rr:" + hashlib.sha256(payload.encode()).hexdigest()


def _retry_after(resp: httpx.Response) -> float | None:
    h = resp.headers.get("retry-after")
    if h:
        try:
            return max(0.0, float(h))
        except ValueError:
            pass
    return None


class VoyageReranker:
    is_fake = False

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_retries: int = MAX_RETRIES,
        timeout: float = 15.0,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("VOYAGE_API_KEY", "")
        self.model = model
        self._transport = transport
        self._sleep = sleep
        self.max_retries = max_retries
        self._timeout = timeout
        self.requests_made = 0

    def __repr__(self) -> str:  # never leak the key
        return f"VoyageReranker(model={self.model!r})"

    async def rerank(self, query: str, docs: list[str], top_n: int) -> list[RerankResult]:
        if not docs:
            return []
        if len(docs) > MAX_DOCUMENTS:
            raise RerankError("INVALID_REQUEST", f"too many documents ({len(docs)} > {MAX_DOCUMENTS})", retryable=False)
        if not self._api_key:
            raise RerankError("AUTH", "VOYAGE_API_KEY is not set", retryable=False)
        body = {
            "query": query,
            "documents": docs,
            "model": self.model,
            "top_k": max(1, min(top_n, len(docs))),
            "return_documents": False,
            "truncation": True,
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        attempt = 0
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            while True:
                self.requests_made += 1
                try:
                    resp = await client.post(ENDPOINT, json=body, headers=headers)
                except httpx.HTTPError as exc:
                    err = RerankError("TRANSIENT_PROVIDER", f"rerank request failed ({type(exc).__name__})", retryable=True)
                    if attempt >= self.max_retries:
                        raise err from None
                    await self._sleep(self._backoff(attempt, None))
                    attempt += 1
                    continue
                if resp.status_code == 200:
                    return self._parse(resp, len(docs))
                err = self._classify(resp)
                if not err.retryable or attempt >= self.max_retries:
                    raise err
                await self._sleep(self._backoff(attempt, _retry_after(resp)))
                attempt += 1

    @staticmethod
    def _backoff(attempt: int, hint: float | None) -> float:
        if hint is not None:
            return min(hint, 30.0)
        return min(0.5 * 2.0**attempt, 8.0) * (0.5 + random.random() / 2)

    @staticmethod
    def _classify(resp: httpx.Response) -> RerankError:
        code = resp.status_code
        try:
            detail = str(resp.json().get("detail", ""))[:200]
        except Exception:  # noqa: BLE001
            detail = ""
        suffix = f": {detail}" if detail else ""
        if code == 429:
            return RerankError("RATE_LIMITED", f"rerank provider rate limited (HTTP 429){suffix}", retryable=True)
        if code in (401, 403):
            return RerankError("AUTH", f"rerank provider rejected credentials (HTTP {code})", retryable=False)
        if code in (408, 409) or code >= 500:
            return RerankError("TRANSIENT_PROVIDER", f"rerank provider error (HTTP {code}){suffix}", retryable=True)
        return RerankError("INVALID_REQUEST", f"rerank request rejected (HTTP {code}){suffix}", retryable=False)

    @staticmethod
    def _parse(resp: httpx.Response, n_docs: int) -> list[RerankResult]:
        try:
            data = resp.json()["data"]
            out = [RerankResult(int(d["index"]), float(d["relevance_score"])) for d in data]
        except Exception:  # noqa: BLE001
            raise RerankError("INVALID_REQUEST", "malformed rerank response", retryable=False) from None
        if any(r.index < 0 or r.index >= n_docs for r in out) or len({r.index for r in out}) != len(out):
            raise RerankError("INVALID_REQUEST", "rerank response has invalid indices", retryable=False)
        return sorted(out, key=lambda r: (-r.score, r.index))


_TOKEN = re.compile(r"\w+", re.UNICODE)


class FakeReranker:
    """DETERMINISTIC TEST DOUBLE: score = fraction of distinct query tokens present in the doc."""

    is_fake = True
    model = "fake-reranker"

    def __init__(self, *, fail_with: RerankError | None = None) -> None:
        self.fail_with = fail_with
        self.calls = 0

    async def rerank(self, query: str, docs: list[str], top_n: int) -> list[RerankResult]:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        q = set(_TOKEN.findall(query.lower()))
        scored = []
        for i, d in enumerate(docs):
            toks = set(_TOKEN.findall(d.lower()))
            scored.append(RerankResult(i, len(q & toks) / len(q) if q else 0.0))
        scored.sort(key=lambda r: (-r.score, r.index))
        return scored[: max(0, top_n)]


class CachedReranker:
    """Cache wrapper (D18). `cache` is either DiskCache-like (`get_or_compute`) or duck-typed
    `get(key)` / `put(key, value)`. The full ranking of the docs is cached (the inner reranker is
    always asked for all docs) so the key need not include top_n. In replay mode
    (SCRIBE_CACHE_MODE=replay) a miss raises CacheMiss and the provider is never called."""

    def __init__(self, inner: Reranker, cache: Any, mode: str | None = None) -> None:
        self.inner = inner
        self.cache = cache
        self.mode = mode or getattr(cache, "mode", None) or os.environ.get("SCRIBE_CACHE_MODE") or "record"
        self.model = inner.model
        self.is_fake = inner.is_fake
        self.hits = 0
        self.misses = 0

    async def rerank(self, query: str, docs: list[str], top_n: int) -> list[RerankResult]:
        if not docs:
            return []
        key = rerank_cache_key(self.model, query, docs)

        async def compute() -> list[list[float]]:
            full = await self.inner.rerank(query, docs, len(docs))
            return [[r.index, r.score] for r in full]

        if hasattr(self.cache, "get_or_compute"):
            raw = await self.cache.get_or_compute(key, compute)
        else:
            raw = self.cache.get(key)
            if raw is None:
                self.misses += 1
                if self.mode == "replay":
                    raise CacheMiss(f"rerank cache miss for {key} in replay mode (SCRIBE_CACHE_MODE=replay)")
                raw = await compute()
                self.cache.put(key, raw)
            else:
                self.hits += 1
        results = [RerankResult(int(i), float(s)) for i, s in raw]
        results.sort(key=lambda r: (-r.score, r.index))
        return results[: max(0, top_n)]


def truncate_to_tokens(text: str, max_tokens: int, count_tokens: Callable[[str], int]) -> str:
    """Longest prefix (cut at whitespace when possible) with count_tokens(prefix) <= max_tokens."""
    if count_tokens(text) <= max_tokens:
        return text
    lo, hi = 0, len(text)
    while lo < hi:  # largest n with count(text[:n]) <= max_tokens
        mid = (lo + hi + 1) // 2
        if count_tokens(text[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    cut = text[:lo]
    ws = max(cut.rfind(" "), cut.rfind("\n"))
    if ws > lo // 2:
        cut = cut[:ws]
    return cut.rstrip()


def rerank_document(rc: RankedCandidate, max_tokens: int, count_tokens: Callable[[str], int]) -> str:
    c = rc.candidate
    doc = f"{c.heading_text}\n{c.content}" if c.heading_text else c.content
    return truncate_to_tokens(doc, max_tokens, count_tokens)


async def rerank_candidates(
    reranker: Reranker,
    query: str,
    ranked: list[RankedCandidate],
    top_n: int = 20,
    *,
    max_doc_tokens: int = DEFAULT_MAX_DOC_TOKENS,
    count_tokens: Callable[[str], int] | None = None,
) -> tuple[list[RankedCandidate], list[str]]:
    """Rerank the first `top_n` fused candidates. Returns (full ordered list, degraded labels).

    Output order: reranked head (rerank_rank 1..m), then the un-reranked tail in fused order.
    On ANY RerankError the fused order is returned unchanged with ["rerank_unavailable"]
    (INV-16). Inputs are not mutated. CacheMiss (replay mode) deliberately propagates."""
    if count_tokens is None:
        from scribe_ingest.tokens import count_tokens as _ct

        count_tokens = _ct
    ordered = sorted(ranked, key=lambda r: (r.fusion_rank is None, r.fusion_rank or 0))
    head, tail = ordered[:top_n], ordered[top_n:]
    if not head:
        return list(ordered), []
    docs = [rerank_document(r, max_doc_tokens, count_tokens) for r in head]
    try:
        results = await reranker.rerank(query, docs, len(head))
        if any(not 0 <= r.index < len(head) for r in results) or len({r.index for r in results}) != len(results):
            raise RerankError("INVALID_REQUEST", "reranker returned invalid indices", retryable=False)
    except RerankError:
        return list(ordered), [DEGRADED_RERANK]
    results = sorted(results, key=lambda r: (-r.score, r.index))
    out: list[RankedCandidate] = []
    seen: set[int] = set()
    for pos, res in enumerate(results, start=1):
        seen.add(res.index)
        out.append(dataclasses.replace(head[res.index], rerank_rank=pos, rerank_score=res.score))
    # anything the reranker omitted stays behind the scored ones, in fused order
    out.extend(r for i, r in enumerate(head) if i not in seen)
    out.extend(tail)
    return out, []
