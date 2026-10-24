"""Rule-first query routing and an optional, cache-backed Gemini classifier.

The router is intentionally not wired into retrieval yet. RTE-3 will decide when to
consume these signals in the retrieval pipeline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .lexical import identifier_tokens

ROUTER_MODEL = "gemini-2.5-flash-lite"
ROUTER_PROMPT_VERSION = "query-route-v1"
ROUTER_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ROUTE_TIMEOUT_S = 2.0
CLASSIFIER_THRESHOLD = 0.60

Via = Literal["rules", "classifier", "default"]


class RoutePlan(BaseModel):
    """Immutable, validated retrieval hints produced by rules or a classifier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lexical_weight: float = Field(ge=0.0, le=1.0)
    cross_doc: bool
    table_boost: bool
    strict_gate: bool
    confidence: float = Field(ge=0.0, le=1.0)
    via: Via


DEFAULT_PLAN = RoutePlan(
    lexical_weight=0.5, cross_doc=False, table_boost=False, strict_gate=False, confidence=0.0, via="default"
)

_CROSS_DOC = re.compile(
    r"\b(?:compare|comparison|vs\.?|versus|difference|differences|changed\s+between)\b", re.IGNORECASE
)
_TABLE = re.compile(r"\b(?:tables?|columns?|rows?|rates?|schedules?|per)\b|\bhow\s+much\b", re.IGNORECASE)
_STRICT = re.compile(
    r"\b(?:not\s+in\s+(?:the\s+)?(?:documents?|tables?)|"
    r"not\s+(?:mentioned|covered)\s+in\s+(?:the\s+)?documents?|"
    r"(?:isn['’]t|is\s+not)\s+(?:mentioned|covered)\s+in\s+(?:the\s+)?documents?|"
    r"outside\s+(?:(?:of\s+)?(?:the\s+)?(?:document\s+)?corpus|(?:the\s+)?documents?)|"
    r"according\s+to\s+your\s+opinion|your\s+opinion|what\s+do\s+you\s+think|"
    r"predict|prediction|forecast|future|will\s+it\s+be)\b",
    re.IGNORECASE,
)


def route_by_rules(question: str) -> RoutePlan:
    """Apply deterministic rules, returning the default plan when none match.

    Confidence contributions (capped at .99): identifier .80, cross-document .65,
    table .65, strict-gate .70. The .60 fallback threshold is a configurable
    implementation default pending RTE-3 evaluation.
    """
    identifier = bool(identifier_tokens(question))
    cross_doc = bool(_CROSS_DOC.search(question))
    table_boost = bool(_TABLE.search(question))
    strict_gate = bool(_STRICT.search(question))
    confidence = min(
        0.99,
        (0.80 if identifier else 0.0)
        + (0.65 if cross_doc else 0.0)
        + (0.65 if table_boost else 0.0)
        + (0.70 if strict_gate else 0.0),
    )
    if confidence == 0:
        return DEFAULT_PLAN
    return RoutePlan(
        lexical_weight=0.9 if identifier else 0.5,
        cross_doc=cross_doc,
        table_boost=table_boost,
        strict_gate=strict_gate,
        confidence=confidence,
        via="rules",
    )


class Classifier(Protocol):
    async def classify(self, question: str) -> RoutePlan: ...


class ClassifierCache(Protocol):
    async def get_or_compute(self, key: str, fn: Any) -> Any: ...


def classifier_cache_key(model: str, question: str) -> str:
    payload = json.dumps([model, ROUTER_PROMPT_VERSION, question], ensure_ascii=False, separators=(",", ":"))
    return "route:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def classifier_prompt(question: str) -> str:
    """The user query is the only dynamic content sent to the classifier."""
    return (
        "Classify this question for document retrieval. Return JSON with exactly these fields: "
        "lexical_weight (0 to 1), cross_doc (boolean), table_boost (boolean), "
        "strict_gate (boolean), confidence (0 to 1). Use cross_doc for comparisons between documents, "
        "table_boost for tabular or numeric lookup, and strict_gate when the question asks for "
        "unsupported opinions or predictions.\nQUESTION:\n<<<QUESTION>>>\n"
        + question
        + "\n<<<END QUESTION>>>"
    )


class GeminiFlashLiteClassifier:
    """Gemini Flash Lite classifier; callers must inject a response cache."""

    def __init__(
        self,
        cache: ClassifierCache,
        *,
        api_key: str | None = None,
        model: str = ROUTER_MODEL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = ROUTE_TIMEOUT_S,
    ) -> None:
        self.cache = cache
        self._api_key = api_key if api_key is not None else os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY", "")
        self.model = model
        self._transport = transport
        self._timeout = timeout

    async def classify(self, question: str) -> RoutePlan:
        key = classifier_cache_key(self.model, question)

        async def compute() -> dict[str, Any]:
            if not self._api_key:
                raise RuntimeError("classifier credentials are not configured")
            body = {
                "contents": [{"role": "user", "parts": [{"text": classifier_prompt(question)}]}],
                "generationConfig": {
                    "temperature": 0,
                    "responseMimeType": "application/json",
                    "responseSchema": {
                        "type": "OBJECT",
                        "properties": {
                            "lexical_weight": {"type": "NUMBER"},
                            "cross_doc": {"type": "BOOLEAN"},
                            "table_boost": {"type": "BOOLEAN"},
                            "strict_gate": {"type": "BOOLEAN"},
                            "confidence": {"type": "NUMBER"},
                        },
                        "required": ["lexical_weight", "cross_doc", "table_boost", "strict_gate", "confidence"],
                    },
                },
            }
            headers = {"x-goog-api-key": self._api_key, "content-type": "application/json"}
            try:
                async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                    response = await client.post(ROUTER_ENDPOINT.format(model=self.model), json=body, headers=headers)
            except httpx.HTTPError as exc:
                raise RuntimeError(f"classifier request failed ({type(exc).__name__})") from None
            if response.status_code != 200:
                raise RuntimeError(f"classifier provider error (HTTP {response.status_code})")
            try:
                parts = response.json()["candidates"][0]["content"]["parts"]
                return json.loads("".join(part.get("text", "") for part in parts if isinstance(part, dict)))
            except (KeyError, IndexError, TypeError, ValueError):
                raise RuntimeError("malformed classifier response") from None

        raw = await self.cache.get_or_compute(key, compute)
        if isinstance(raw, RoutePlan):
            raw = raw.model_dump()
        if not isinstance(raw, dict):
            raise ValueError("malformed cached classifier response")
        raw = {**raw, "via": "classifier"}
        return RoutePlan.model_validate(raw)


async def plan_route(
    question: str,
    classifier: Classifier | None = None,
    *,
    threshold: float = CLASSIFIER_THRESHOLD,
    timeout: float = ROUTE_TIMEOUT_S,
) -> RoutePlan:
    """Use confident rules first, then an injected classifier; failures use the default plan."""
    ruled = route_by_rules(question)
    if ruled.confidence >= threshold:
        return ruled
    if classifier is None:
        return DEFAULT_PLAN
    try:
        classified = await asyncio.wait_for(classifier.classify(question), timeout=timeout)
        if not isinstance(classified, RoutePlan):
            classified = RoutePlan.model_validate({**classified, "via": "classifier"})
        return classified.model_copy(update={"via": "classifier"})
    except Exception:  # provider, cache miss, validation, and timeout all fail closed to the default
        return DEFAULT_PLAN
