import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from scribe_intel.retrieval.router import (
    DEFAULT_PLAN,
    ROUTER_ENDPOINT,
    ROUTER_MODEL,
    GeminiFlashLiteClassifier,
    RoutePlan,
    classifier_cache_key,
    classifier_prompt,
    plan_route,
    route_by_rules,
)


# Literal hand labels make the rule behavior reviewable and keep coverage independent of generated data.
RULE_CASES = [
    ("identifier", "What does FUEL-SUR mean?", .9, False, False, False),
    ("identifier", "Explain ERR_AUTH_403", .9, False, False, False),
    ("identifier", "Find INV-2047", .9, False, False, False),
    ("identifier", "What is ISO27001?", .9, False, False, False),
    ("identifier", "Where is NW-COLD-04 described?", .9, False, False, False),
    ("identifier", "What does err-auth-403 indicate?", .9, False, False, False),
    ("identifier", "Look up SOC2024", .9, False, False, False),
    ("identifier", "Explain A4B7", .9, False, False, False),
    ("identifier", "Is Err_403_x listed?", .9, False, False, False),
    ("identifier", "What is API_KEY_2?", .9, False, False, False),
    ("identifier", "Locate DOC-12A", .9, False, False, False),
    ("identifier", "Define X509_CERT", .9, False, False, False),
    ("cross", "Compare the two policies", .5, True, False, False),
    ("cross", "What changed between the versions?", .5, True, False, False),
    ("cross", "What is the difference in coverage?", .5, True, False, False),
    ("cross", "Policy A vs policy B", .5, True, False, False),
    ("cross", "Show the differences between editions", .5, True, False, False),
    ("cross", "Compare older and newer terms", .5, True, False, False),
    ("cross", "Which provisions changed between documents?", .5, True, False, False),
    ("cross", "What is the difference between the plans?", .5, True, False, False),
    ("cross", "Compare the rates in both files", .5, True, True, False),
    ("cross", "Version 1 versus version 2", .5, True, False, False),
    ("cross", "Can you compare these contracts?", .5, True, False, False),
    ("cross", "What changed between the old and new handbook?", .5, True, False, False),
    ("table", "What does the table say about leave?", .5, False, True, False),
    ("table", "Which column lists the deadline?", .5, False, True, False),
    ("table", "Find the row for contractors", .5, False, True, False),
    ("table", "What is the interest rate?", .5, False, True, False),
    ("table", "Show the payment schedule", .5, False, True, False),
    ("table", "How much is the annual allowance?", .5, False, True, False),
    ("table", "What is the fee per month?", .5, False, True, False),
    ("table", "Give me the rate per year", .5, False, True, False),
    ("table", "Which table has the totals?", .5, False, True, False),
    ("table", "What does the second row contain?", .5, False, True, False),
    ("table", "How much notice is required?", .5, False, True, False),
    ("table", "Which column contains the rate per unit?", .5, False, True, False),
    ("strict", "What is not in the documents?", .5, False, False, True),
    ("strict", "What is not mentioned in the documents?", .5, False, False, True),
    ("strict", "What isn't covered in the documents?", .5, False, False, True),
    ("strict", "What is outside of the document corpus?", .5, False, False, True),
    ("strict", "Tell me something outside the documents", .5, False, False, True),
    ("strict", "According to your opinion, is this fair?", .5, False, False, True),
    ("strict", "What do you think about this policy?", .5, False, False, True),
    ("strict", "Predict next year's rate", .5, False, True, True),
    ("strict", "Give a prediction for the market", .5, False, False, True),
    ("strict", "Forecast how costs will change", .5, False, False, True),
    ("strict", "What will happen in the future?", .5, False, False, True),
    ("strict", "Will it be approved next year?", .5, False, False, True),
    ("strict", "What is your opinion of the forecast?", .5, False, False, True),
    ("strict", "Can you predict future eligibility?", .5, False, False, True),
    ("strict", "Is that fact outside the documents?", .5, False, False, True),
    ("mixed", "Compare table rows for ISO27001", .9, True, True, False),
    ("mixed", "What changed between ERR_AUTH_403 and ERR_AUTH_404?", .9, True, False, False),
    ("mixed", "How much is the rate per month in INV-2047?", .9, False, True, False),
    ("mixed", "Compare future rate schedules between files", .5, True, True, True),
    ("mixed", "Predict whether DOC-12A will be approved", .9, False, False, True),
    ("mixed", "What is not in the table for SOC2024?", .9, False, True, True),
    ("mixed", "Compare the rate table and predict next year's fee", .5, True, True, True),
    ("mixed", "What does row ERR_AUTH_403 say about future rates?", .9, False, True, True),
    ("mixed", "What is the difference per month for ISO27001?", .9, True, True, False),
    ("mixed", "Compare opinion tables outside the documents", .5, True, True, True),
    ("mixed", "Find the schedule row for NW-COLD-04", .9, False, True, False),
    ("mixed", "What changed between tables in DOC-12A?", .9, True, True, False),
    ("mixed", "How much is the table rate, and what do you think?", .5, False, True, True),
    ("mixed", "Compare policy columns and future forecasts", .5, True, True, True),
    ("mixed", "Predict future changes to INV-2047", .9, False, False, True),
    ("mixed", "What is the difference in the schedule for SOC2024?", .9, True, True, False),
    ("mixed", "Is the table row outside the documents?", .5, False, True, True),
    ("mixed", "What changed between the rate columns for A4B7?", .9, True, True, False),
]
assert len(RULE_CASES) >= 60


@pytest.mark.parametrize("label,question,lexical,cross_doc,table_boost,strict_gate", RULE_CASES)
def test_literal_hand_labelled_rule_queries(label, question, lexical, cross_doc, table_boost, strict_gate):
    plan = route_by_rules(question)
    assert plan.via == "rules", label
    assert plan.lexical_weight == lexical
    assert plan.cross_doc is cross_doc
    assert plan.table_boost is table_boost
    assert plan.strict_gate is strict_gate
    assert plan.confidence > 0


def test_no_rule_returns_frozen_default_and_confidence_is_validated():
    assert route_by_rules("When does enrollment start?") == DEFAULT_PLAN
    with pytest.raises(ValidationError):
        RoutePlan(lexical_weight=1.1, cross_doc=False, table_boost=False, strict_gate=False,
                  confidence=0.5, via="rules")
    with pytest.raises(ValidationError):
        DEFAULT_PLAN.cross_doc = True


class FakeClassifier:
    def __init__(self, result=None, error=None, delay=0):
        self.result = result or RoutePlan(lexical_weight=.7, cross_doc=False, table_boost=True,
                                          strict_gate=False, confidence=.77, via="classifier")
        self.error = error
        self.delay = delay
        self.questions = []

    async def classify(self, question):
        self.questions.append(question)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


async def test_classifier_only_runs_below_threshold_and_marks_route():
    classifier = FakeClassifier()
    routed = await plan_route("When does enrollment start?", classifier)
    assert routed.via == "classifier" and routed.table_boost
    assert classifier.questions == ["When does enrollment start?"]
    assert (await plan_route("What does INV-2047 mean?", classifier)).via == "rules"
    assert len(classifier.questions) == 1


@pytest.mark.parametrize("error", [RuntimeError("provider failed"), ValueError("bad result")])
async def test_classifier_error_returns_default(error):
    assert await plan_route("When does enrollment start?", FakeClassifier(error=error)) == DEFAULT_PLAN


async def test_classifier_timeout_returns_default():
    assert await plan_route("When does enrollment start?", FakeClassifier(delay=.1), timeout=.001) == DEFAULT_PLAN


async def test_invalid_classifier_plan_returns_default():
    invalid = {"lexical_weight": 1.4, "cross_doc": False, "table_boost": False,
               "strict_gate": False, "confidence": .9, "via": "classifier"}
    assert await plan_route("When does enrollment start?", FakeClassifier(result=invalid)) == DEFAULT_PLAN


def test_classifier_prompt_contains_only_question_input():
    question = "When does enrollment start?"
    prompt = classifier_prompt(question)
    assert question in prompt
    assert "DOCUMENT TEXT MUST NOT APPEAR" not in prompt


class MemoryCache:
    def __init__(self, values=None, error=None):
        self.values = values or {}
        self.error = error
        self.calls = 0

    async def get_or_compute(self, key, fn):
        self.calls += 1
        if key in self.values:
            return self.values[key]
        if self.error:
            raise self.error
        value = await fn()
        self.values[key] = value
        return value


async def test_gemini_flash_lite_rest_payload_secret_boundary_and_cache():
    seen = {}
    provider_calls = []
    key = "test-secret-key"

    def handler(request):
        provider_calls.append(1)
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        answer = {"lexical_weight": .72, "cross_doc": False, "table_boost": True,
                  "strict_gate": False, "confidence": .81}
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}}]})

    cache = MemoryCache()
    classifier = GeminiFlashLiteClassifier(cache, api_key=key, transport=httpx.MockTransport(handler))
    first = await classifier.classify("When does enrollment start?")
    second = await classifier.classify("When does enrollment start?")
    assert first == second and first.via == "classifier"
    assert cache.calls == 2
    assert len(provider_calls) == 1
    assert seen["url"] == ROUTER_ENDPOINT.format(model=ROUTER_MODEL)
    assert seen["headers"]["x-goog-api-key"] == key
    assert key not in seen["url"] and key not in json.dumps(seen["body"])
    serialized_prompt = json.dumps(seen["body"])
    assert "When does enrollment start?" in serialized_prompt
    assert "document text" not in serialized_prompt.lower()


async def test_gemini_classifier_cache_miss_and_provider_error_degrade_to_default():
    miss = MemoryCache(error=RuntimeError("cache miss"))
    assert await plan_route("When does enrollment start?", GeminiFlashLiteClassifier(miss, api_key="x")) == DEFAULT_PLAN

    cache = MemoryCache()
    bad = GeminiFlashLiteClassifier(cache, api_key="x", transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    assert await plan_route("When does enrollment start?", bad) == DEFAULT_PLAN


async def test_gemini_classifier_replay_style_cache_hit_never_calls_provider():
    cache = MemoryCache()
    cache.values[classifier_cache_key(ROUTER_MODEL, "When does enrollment start?")] = {
        "lexical_weight": .6, "cross_doc": False, "table_boost": False,
        "strict_gate": False, "confidence": .7,
    }
    classifier = GeminiFlashLiteClassifier(cache, api_key="x", transport=httpx.MockTransport(
        lambda _: pytest.fail("provider must not be called on cache hit")))
    assert (await classifier.classify("When does enrollment start?")).via == "classifier"
