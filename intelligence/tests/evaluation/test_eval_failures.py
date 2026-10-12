"""T30: the failure classifier is a pure function over a case result row and the gold case."""
from __future__ import annotations

import pytest
from scribe_eval.dataset import Case
from scribe_eval.failures import (
    FAILURE_CATEGORIES, FALSE_ABSTENTION, MISSED_ABSTENTION, NOT_RETRIEVED, PARSE_LOST, RANKED_TOO_LOW,
    UNCLASSIFIED, UNSUPPORTED_CLAIM, WRONG_VERSION, classify_failure, count_failures,
)

QUOTE = "Annual leave is 25 days per year"


def case(answerable=True, forbidden=()):
    return Case(
        id="c1", split="dev", category="single-fact", question="How much leave?", answerable=answerable,
        reference_answer="25 days" if answerable else "", must_include=["25"] if answerable else [],
        evidence=[{"doc": "hr.md", "version": 1, "quote": QUOTE, "grade": 2}] if answerable else [],
        forbidden=[{"doc": d} for d in forbidden],
    )


def item(text, doc="other.md"):
    return {"chunk_id": text[:4], "doc": doc, "text": text, "score": 0.5, "method": "dense"}


GOLD = item(f"Policy: {QUOTE}. More text.", "hr.md")
NOISE = item("Unrelated paragraph about parking.")


def ranking(*items):
    return {"ranking": list(items), "metrics": {}}


def test_gold_in_top_five_passes():
    assert classify_failure(case(), ranking(NOISE, GOLD)) is None
    assert classify_failure(case(), ranking(GOLD)) is None


def test_not_retrieved_when_no_item_matches():
    assert classify_failure(case(), ranking(NOISE, item("other"))) == NOT_RETRIEVED


def test_ranked_too_low_when_first_match_beyond_cutoff():
    fillers = [item(f"filler {i}") for i in range(5)]
    assert classify_failure(case(), ranking(*fillers, GOLD)) == RANKED_TOO_LOW
    assert classify_failure(case(), ranking(*fillers[:4], GOLD)) is None  # rank 5 still counts
    assert classify_failure(case(), ranking(*fillers, GOLD), low_rank_cutoff=10) is None


def test_wrong_version_when_top_item_is_a_forbidden_doc():
    old = item("Annual leave is 20 days per year", "hr-old.md")
    assert classify_failure(case(forbidden=["hr-old.md"]), ranking(old, GOLD)) == WRONG_VERSION
    # gold at rank 1 from a forbidden doc name is not blamed on the version
    assert classify_failure(case(forbidden=["hr.md"]), ranking(GOLD)) is None
    # an answer citing a forbidden doc
    row = {"abstained": False, "claims": [], "metrics": {"correct": 1.0, "wrong_version_citations": 1.0}}
    assert classify_failure(case(forbidden=["hr-old.md"]), row) == WRONG_VERSION


def test_parse_lost_takes_precedence_over_retrieval_symptoms():
    row = {**ranking(NOISE), "parse_lost_docs": ["hr.md"]}
    assert classify_failure(case(), row) == PARSE_LOST
    # a lost document unrelated to this case's evidence is ignored
    assert classify_failure(case(), {**ranking(NOISE), "parse_lost_docs": ["other.pdf"]}) == NOT_RETRIEVED
    # a lost doc does not turn a passing row into a failure
    assert classify_failure(case(), {**ranking(GOLD), "parse_lost_docs": ["hr.md"]}) is None


def test_false_abstention_and_missed_abstention():
    assert classify_failure(case(), {"abstained": True, "claims": [], "metrics": {"correct": 0.0}}) == FALSE_ABSTENTION
    assert classify_failure(case(False), {"abstained": False, "claims": [], "metrics": {"correct": 0.0}}) == MISSED_ABSTENTION
    assert classify_failure(case(False), {"abstained": True, "claims": [], "metrics": {"correct": 1.0}}) is None


def test_unsupported_claim_in_an_answered_case():
    row = {"abstained": False, "metrics": {"correct": 1.0, "wrong_version_citations": 0.0},
           "claims": [{"status": "VERIFIED"}, {"status": "UNSUPPORTED"}]}
    assert classify_failure(case(), row) == UNSUPPORTED_CLAIM
    ok = {"abstained": False, "metrics": {"correct": 1.0}, "claims": [{"status": "VERIFIED"}, {"status": "PARTIAL"}]}
    assert classify_failure(case(), ok) is None


def test_retrieval_cause_outranks_answer_symptom():
    row = {**ranking(NOISE), "abstained": True, "claims": [], "metrics": {"correct": 0.0}}
    assert classify_failure(case(), row) == NOT_RETRIEVED


def test_wrong_answer_with_good_retrieval_and_clean_claims_is_unclassified():
    row = {**ranking(GOLD), "abstained": False, "claims": [{"status": "VERIFIED"}], "metrics": {"correct": 0.0}}
    assert classify_failure(case(), row) == UNCLASSIFIED


@pytest.mark.parametrize("row", [None, "x", 3, [], {"ranking": [1, 2]}, {"ranking": ["a"]}])
def test_unreadable_rows_are_unclassified_not_guessed(row):
    assert classify_failure(case(), row) == UNCLASSIFIED


def test_answerable_case_with_no_data_is_unclassified_and_unanswerable_retrieval_never_fails():
    assert classify_failure(case(), {}) == UNCLASSIFIED
    assert classify_failure(case(False), {"ranking": [NOISE], "metrics": None}) is None
    assert classify_failure(case(False), {}) is None


def test_empty_ranking_for_answerable_case_is_not_guessed():
    # nothing stored: we cannot tell "not retrieved" from "ranking not recorded"
    assert classify_failure(case(), {"ranking": []}) == UNCLASSIFIED


def test_categories_are_the_documented_set_and_counts():
    assert set(FAILURE_CATEGORIES) == {NOT_RETRIEVED, RANKED_TOO_LOW, WRONG_VERSION, PARSE_LOST, UNSUPPORTED_CLAIM,
                                       FALSE_ABSTENTION, MISSED_ABSTENTION, UNCLASSIFIED}
    assert count_failures([NOT_RETRIEVED, None, NOT_RETRIEVED, PARSE_LOST]) == {NOT_RETRIEVED: 2, PARSE_LOST: 1}
