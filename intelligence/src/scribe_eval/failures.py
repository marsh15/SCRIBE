"""Failure categories for the eval dashboard (T30).

``classify_failure`` is a pure function over one case's result row plus the gold case (evidence, forbidden docs). It
returns ``None`` for a case that did not fail, one of ``FAILURE_CATEGORIES`` otherwise, and ``UNCLASSIFIED`` whenever the
inputs do not let it name a cause. It never guesses: a row it cannot read, or a failure none of the rules explains, is
``unclassified``.

Row fields it reads (all optional; which are present depends on the run kind):
  retrieval rows (``results.jsonl``): ``ranking`` (best first, items with ``text``/``doc``), ``metrics`` (``hit@5`` ...).
  answer rows (``answers.jsonl``):    ``abstained``, ``claims`` (each with ``status``), ``metrics`` (``correct``,
                                      ``wrong_version_citations``).
  either:                             ``parse_lost_docs`` (documents whose gold content the parser is known to have lost).

Rules, first match wins (root cause before symptom):
  1. parse lost the content   a gold-evidence document is listed in ``parse_lost_docs``.
  2. wrong version            the top-ranked item comes from a forbidden (superseded) document and no gold evidence is
                              in the rank-1 item; or an answer cited a forbidden document (``wrong_version_citations`` > 0).
  3. not retrieved            a ranking exists and no item in it matches any gold evidence.
  4. ranked too low           gold evidence is in the ranking, but its first match is below ``LOW_RANK_CUTOFF``.
  5. false abstention         answerable case, the answer refused.
  6. missed abstention        unanswerable case, the answer did not refuse.
  7. unsupported claim        an answerable, answered case with a claim that is UNSUPPORTED, UNCITED or INVALID_CITATION.
A retrieval-only row for an unanswerable case never fails (nothing to retrieve); an answer row that is correct and has no
unsupported claim passes.
"""

from __future__ import annotations

from typing import Any, Iterable

from .dataset import Case
from .metrics import RankedItem, match_matrix

NOT_RETRIEVED = "not retrieved"
RANKED_TOO_LOW = "ranked too low"
WRONG_VERSION = "wrong version"
PARSE_LOST = "parse lost the content"
UNSUPPORTED_CLAIM = "unsupported claim"
FALSE_ABSTENTION = "false abstention"
MISSED_ABSTENTION = "missed abstention"
UNCLASSIFIED = "unclassified"

FAILURE_CATEGORIES: tuple[str, ...] = (
    NOT_RETRIEVED, RANKED_TOO_LOW, WRONG_VERSION, PARSE_LOST,
    UNSUPPORTED_CLAIM, FALSE_ABSTENTION, MISSED_ABSTENTION, UNCLASSIFIED,
)
LOW_RANK_CUTOFF = 5  # gold evidence first found at rank > 5 counts as "ranked too low"
BAD_CLAIM_STATUSES = frozenset({"UNSUPPORTED", "UNCITED", "INVALID_CITATION"})


def _docs(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(v) for v in value}
    return set()


def classify_failure(case: Case, row: Any, *, low_rank_cutoff: int = LOW_RANK_CUTOFF) -> str | None:
    if not isinstance(row, dict):
        return UNCLASSIFIED
    answerable = bool(case.answerable and case.evidence)
    ranking_raw = row.get("ranking")
    has_ranking = isinstance(ranking_raw, list) and len(ranking_raw) > 0
    has_answer = isinstance(row.get("abstained"), bool)
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}

    # What the row says about itself decides whether it failed at all.
    gold_docs = {e.doc for e in case.evidence}
    forbidden = {f.doc for f in case.forbidden}
    claims = row.get("claims") if isinstance(row.get("claims"), list) else []

    if not answerable and not has_answer:
        return None  # retrieval-only row for an unanswerable case: nothing to retrieve, nothing to fail

    # --- retrieval stage ------------------------------------------------------------------------------------
    first_gold_rank: int | None = None
    top_is_forbidden = False
    if has_ranking:
        items: list[RankedItem] = []
        for d in ranking_raw:
            if not isinstance(d, dict):
                return UNCLASSIFIED
            items.append(RankedItem.from_dict(d))
        matches = match_matrix(case, items) if answerable else []
        first_gold_rank = next((r for r, s in enumerate(matches, 1) if s), None)
        top_is_forbidden = bool(items) and items[0].doc in forbidden and not (matches and matches[0])
    retrieval_failed = False
    if answerable and has_ranking:
        retrieval_failed = first_gold_rank is None or first_gold_rank > low_rank_cutoff
    wrong_cites = metrics.get("wrong_version_citations")
    answer_failed = False
    if has_answer:
        abstained = bool(row["abstained"])
        if answerable:
            answer_failed = abstained or metrics.get("correct") == 0.0 or any(
                isinstance(c, dict) and c.get("status") in BAD_CLAIM_STATUSES for c in claims)
        else:
            answer_failed = not abstained
    wrong_version = (answerable and top_is_forbidden) or (isinstance(wrong_cites, (int, float)) and wrong_cites > 0)

    if not (retrieval_failed or answer_failed or wrong_version):
        if answerable and not has_ranking and not has_answer:
            return UNCLASSIFIED  # an answerable case with no evidence of either kind: cannot say it passed
        return None

    # --- name the cause -------------------------------------------------------------------------------------
    if answerable and gold_docs & _docs(row.get("parse_lost_docs")):
        return PARSE_LOST
    if wrong_version:
        return WRONG_VERSION
    if answerable and has_ranking and first_gold_rank is None:
        return NOT_RETRIEVED
    if answerable and has_ranking and first_gold_rank is not None and first_gold_rank > low_rank_cutoff:
        return RANKED_TOO_LOW
    if has_answer and not answerable and not row["abstained"]:
        return MISSED_ABSTENTION
    if has_answer and answerable and row["abstained"]:
        return FALSE_ABSTENTION
    if has_answer and answerable and any(
            isinstance(c, dict) and c.get("status") in BAD_CLAIM_STATUSES for c in claims):
        return UNSUPPORTED_CLAIM
    return UNCLASSIFIED


def count_failures(categories: Iterable[str | None]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in categories:
        if c:
            out[c] = out.get(c, 0) + 1
    return out
