"""The single "nothing is published from unreviewed data" check, shared by validate, score, answer and publish-run."""

from __future__ import annotations

from typing import Any, Iterable

REVIEW_HINT = (
    "Review them with: uv run --project intelligence scribe-eval review --items <dataset>/cases.jsonl "
    "--kind case --corpus fixtures/corpus"
)


def _get(case: Any, key: str) -> Any:
    return case.get(key) if isinstance(case, dict) else getattr(case, key, None)


def unreviewed_ids(cases: Iterable[Any]) -> list[str]:
    """Ids of every case whose ``reviewed`` is not exactly True (absent or None counts as unreviewed).

    Accepts ``Case`` objects or plain dicts, in input order.
    """
    return [str(_get(c, "id")) for c in cases if _get(c, "reviewed") is not True]


def refusal_message(ids: list[str], total: int, action: str) -> str:
    shown = ", ".join(ids[:10]) + (f", ... (+{len(ids) - 10} more)" if len(ids) > 10 else "")
    return (
        f"ERROR: refusing to {action}: {len(ids)} of {total} case(s) are not human-reviewed "
        f"(reviewed is not true): {shown}. {REVIEW_HINT}"
    )
