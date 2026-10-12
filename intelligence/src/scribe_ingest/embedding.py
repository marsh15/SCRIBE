"""Embedding client (DESIGN A2, D18).

Task format
-----------
Google's documentation (ai.google.dev/gemini-api/docs/embeddings, checked for T12) states that
`gemini-embedding-2` does NOT accept the `task_type` field. The task is expressed as a text
prefix instead:

* document: ``title: {title or "none"} | text: {content}``
* query:    ``task: search result | query: {content}``

`TaskFormat` holds these two templates; the defaults follow the docs and the same strings must
be used for documents and queries of one index (the format name is part of the cache key).
NOTE: the seeded `gemini-embedding-2-1536` profile config (0011) still lists RETRIEVAL_DOCUMENT
task types, which is wrong for this model; profiles are immutable, so see T12-notes.md.

The API key is read from GOOGLE_GENERATIVE_AI_API_KEY and sent only in the `x-goog-api-key`
header; it never appears in URLs, errors or logs.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import random
import re
import struct
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Protocol, Sequence

import httpx

DEFAULT_MODEL = "gemini-embedding-2"
DEFAULT_DIMS = 1536
BATCH_SIZE = 100
MAX_RETRIES = 5
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"

ErrorCode = Literal["RATE_LIMITED", "TRANSIENT_PROVIDER", "INVALID_REQUEST", "AUTH"]


class EmbeddingError(Exception):
    def __init__(self, code: ErrorCode, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.retryable = retryable

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class TaskFormat:
    name: str = "gemini-embedding-2-prefix-v1"
    document: str = "title: {title} | text: {text}"
    query: str = "task: search result | query: {text}"
    empty_title: str = "none"

    def format_document(self, text: str, title: str | None = None) -> str:
        return self.document.format(title=(title or "").strip() or self.empty_title, text=text)

    def format_query(self, text: str) -> str:
        return self.query.format(text=text)


class EmbeddingCache(Protocol):
    def get(self, key: str) -> list[float] | None: ...
    def put(self, key: str, value: list[float]) -> None: ...


def cache_key(model: str, dims: int, task: str, text: str) -> str:
    """D18: (model, dims, task, sha256(text))."""
    return f"emb:{model}:{dims}:{task}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def l2_normalize(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if not math.isfinite(norm) or norm == 0:
        raise EmbeddingError("INVALID_REQUEST", "provider returned a zero or non-finite vector", retryable=False)
    return [x / norm for x in vec]


class Embedder(Protocol):
    is_fake: bool
    model: str
    dimensions: int

    async def embed_documents(self, texts: Sequence[str], titles: Sequence[str | None] | None = None) -> list[list[float]]: ...
    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]: ...


def _retry_after(resp: httpx.Response) -> float | None:
    header = resp.headers.get("retry-after")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    try:
        body = resp.json()
        for d in body.get("error", {}).get("details", []):
            m = re.fullmatch(r"([\d.]+)s", str(d.get("retryDelay", "")))
            if m:
                return float(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    return None


class GeminiEmbedder:
    is_fake = False

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        dimensions: int = DEFAULT_DIMS,
        task_format: TaskFormat | None = None,
        cache: EmbeddingCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        batch_size: int = BATCH_SIZE,
        max_retries: int = MAX_RETRIES,
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY", "")
        self.model = model
        self.dimensions = dimensions
        self.task_format = task_format or TaskFormat()
        self.cache = cache
        self._transport = transport
        self._sleep = sleep
        self.batch_size = batch_size
        self.max_retries = max_retries
        self._timeout = timeout
        self.requests_made = 0
        self.texts_embedded = 0  # provider-embedded (cache misses)

    # -- public ----------------------------------------------------------
    async def embed_documents(self, texts: Sequence[str], titles: Sequence[str | None] | None = None) -> list[list[float]]:
        titles = titles or [None] * len(texts)
        if len(titles) != len(texts):
            raise ValueError("titles must match texts")
        formatted = [self.task_format.format_document(t, ti) for t, ti in zip(texts, titles)]
        return await self._embed(formatted, "document")

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed([self.task_format.format_query(t) for t in texts], "query")

    # -- internals -------------------------------------------------------
    async def _embed(self, formatted: list[str], task: str) -> list[list[float]]:
        task_key = f"{self.task_format.name}/{task}"
        out: list[list[float] | None] = [None] * len(formatted)
        missing: list[int] = []
        for i, text in enumerate(formatted):
            hit = self.cache.get(cache_key(self.model, self.dimensions, task_key, text)) if self.cache else None
            if hit is not None and len(hit) == self.dimensions:
                out[i] = hit
            else:
                missing.append(i)
        if missing:
            if not self._api_key:
                raise EmbeddingError("AUTH", "GOOGLE_GENERATIVE_AI_API_KEY is not set", retryable=False)
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                for start in range(0, len(missing), self.batch_size):
                    idxs = missing[start : start + self.batch_size]
                    vectors = await self._batch(client, [formatted[i] for i in idxs])
                    for i, vec in zip(idxs, vectors):
                        out[i] = vec
                        if self.cache:
                            self.cache.put(cache_key(self.model, self.dimensions, task_key, formatted[i]), vec)
        return out  # type: ignore[return-value]

    async def _batch(self, client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
        body = {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": t}]},
                    "outputDimensionality": self.dimensions,
                }
                for t in texts
            ]
        }
        headers = {"x-goog-api-key": self._api_key, "content-type": "application/json"}
        url = ENDPOINT.format(model=self.model)
        attempt = 0
        while True:
            self.requests_made += 1
            try:
                resp = await client.post(url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                err = EmbeddingError(
                    "TRANSIENT_PROVIDER", f"embedding request failed ({type(exc).__name__})", retryable=True
                )
                if attempt >= self.max_retries:
                    raise err from None
                await self._sleep(self._backoff(attempt, None))
                attempt += 1
                continue
            if resp.status_code == 200:
                self.texts_embedded += len(texts)
                return self._parse(resp, len(texts))
            err = self._classify(resp)
            if not err.retryable or attempt >= self.max_retries:
                raise err
            await self._sleep(self._backoff(attempt, _retry_after(resp)))
            attempt += 1

    @staticmethod
    def _backoff(attempt: int, hint: float | None) -> float:
        if hint is not None:
            return min(hint, 120.0)
        return min(2.0**attempt, 60.0) * (0.5 + random.random() / 2)

    @staticmethod
    def _classify(resp: httpx.Response) -> EmbeddingError:
        code = resp.status_code
        try:
            msg = str(resp.json().get("error", {}).get("message", ""))[:200]
        except Exception:  # noqa: BLE001
            msg = ""
        suffix = f": {msg}" if msg else ""
        if code == 429:
            return EmbeddingError("RATE_LIMITED", f"embedding provider rate limited (HTTP 429){suffix}", retryable=True)
        if code in (401, 403):
            return EmbeddingError("AUTH", f"embedding provider rejected credentials (HTTP {code})", retryable=False)
        if code in (408, 409) or code >= 500:
            return EmbeddingError("TRANSIENT_PROVIDER", f"embedding provider error (HTTP {code}){suffix}", retryable=True)
        return EmbeddingError("INVALID_REQUEST", f"embedding request rejected (HTTP {code}){suffix}", retryable=False)

    def _parse(self, resp: httpx.Response, expected: int) -> list[list[float]]:
        try:
            embeddings = resp.json()["embeddings"]
        except Exception:  # noqa: BLE001
            raise EmbeddingError("INVALID_REQUEST", "malformed embedding response", retryable=False) from None
        if not isinstance(embeddings, list) or len(embeddings) != expected:
            raise EmbeddingError(
                "INVALID_REQUEST",
                f"expected {expected} embeddings, got {len(embeddings) if isinstance(embeddings, list) else 'none'}",
                retryable=False,
            )
        out: list[list[float]] = []
        for e in embeddings:
            values: Any = e.get("values") if isinstance(e, dict) else None
            if not isinstance(values, list) or len(values) != self.dimensions:
                raise EmbeddingError(
                    "INVALID_REQUEST",
                    f"expected {self.dimensions} dimensions, got {len(values) if isinstance(values, list) else 'none'}",
                    retryable=False,
                )
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
                raise EmbeddingError("INVALID_REQUEST", "embedding contains non-finite values", retryable=False)
            out.append(l2_normalize(values))
        return out


class FakeEmbedder:
    """DETERMINISTIC TEST DOUBLE. Hash -> unit vector. Carries no semantic meaning."""

    is_fake = True
    model = "fake-embedder"

    def __init__(self, dimensions: int = DEFAULT_DIMS, fail_after: int | None = None) -> None:
        self.dimensions = dimensions
        self.fail_after = fail_after  # raise after this many texts (test hook)
        self.requests_made = 0
        self.texts_embedded = 0

    def _vec(self, text: str) -> list[float]:
        raw: list[float] = []
        counter = 0
        while len(raw) < self.dimensions:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            raw.extend(v / 2**31 - 1.0 for v in struct.unpack("<8i", digest))
            counter += 1
        return l2_normalize(raw[: self.dimensions])

    async def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.requests_made += max(1, math.ceil(len(texts) / BATCH_SIZE))
        if self.fail_after is not None and self.texts_embedded + len(texts) > self.fail_after:
            raise EmbeddingError("TRANSIENT_PROVIDER", "fake embedder injected failure", retryable=True)
        self.texts_embedded += len(texts)
        return [self._vec(t) for t in texts]

    async def embed_documents(self, texts: Sequence[str], titles: Sequence[str | None] | None = None) -> list[list[float]]:
        titles = titles or [None] * len(texts)
        return await self._embed([f"{ti or ''}\n{t}" for t, ti in zip(texts, titles)])

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(list(texts))


class FakeEmbedderRefused(RuntimeError):
    pass


def database_name(dsn: str) -> str:
    from psycopg.conninfo import conninfo_to_dict

    return str(conninfo_to_dict(dsn).get("dbname") or "")


def embedder_from_env(dsn: str, env: "os._Environ[str] | dict[str, str] | None" = None,
                      cache: EmbeddingCache | None = None) -> Embedder:
    """SCRIBE_EMBEDDER=gemini (default) | fake. `fake` needs SCRIBE_ALLOW_FAKE_EMBEDDINGS=1 AND a
    database whose name contains 'test'; otherwise it is refused loudly (fake vectors in a real
    index would silently destroy retrieval quality)."""
    env = os.environ if env is None else env
    kind = (env.get("SCRIBE_EMBEDDER") or "gemini").lower()
    if kind == "gemini":
        return GeminiEmbedder(api_key=env.get("GOOGLE_GENERATIVE_AI_API_KEY", ""), cache=cache)
    if kind == "fake":
        return require_fake_allowed(dsn, env)
    raise ValueError(f"SCRIBE_EMBEDDER must be 'gemini' or 'fake', got {kind!r}")


def require_fake_allowed(dsn: str, env: "os._Environ[str] | dict[str, str] | None" = None) -> FakeEmbedder:
    env = os.environ if env is None else env
    name = database_name(dsn)
    if env.get("SCRIBE_ALLOW_FAKE_EMBEDDINGS") != "1" or "test" not in name.lower():
        raise FakeEmbedderRefused(
            f"REFUSING to use the fake embedder: it requires SCRIBE_ALLOW_FAKE_EMBEDDINGS=1 and a database "
            f"whose name contains 'test' (database is {name!r})."
        )
    return FakeEmbedder()
