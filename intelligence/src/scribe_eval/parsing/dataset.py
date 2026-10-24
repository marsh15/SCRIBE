"""Parsing comparison field dataset loading and corpus path validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from scribe_eval.review.kinds import FieldItem


class FieldDatasetError(ValueError):
    pass


def safe_doc_path(corpus: str | Path, doc: str) -> Path:
    root = Path(corpus).resolve()
    path = (root / doc).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise FieldDatasetError(f"document path escapes corpus root: {doc!r}") from None
    if not path.is_file():
        raise FieldDatasetError(f"document not found in corpus: {doc!r}")
    if path.suffix.lower() != ".pdf":
        raise FieldDatasetError(f"parsing comparison currently supports PDF documents only: {doc!r}")
    return path


def load_fields(path: str | Path, corpus: str | Path) -> list[dict[str, Any]]:
    """Load JSONL using the review tool's FieldItem schema and ensure safe corpus paths."""
    source = Path(path)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_no, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError("item must be an object")
            item = FieldItem.model_validate(raw).model_dump(exclude_none=True)
            if item.get("page") is not None and item["page"] < 1:
                raise ValueError("page must be a positive one-based page number")
            safe_doc_path(corpus, item["doc"])
        except (json.JSONDecodeError, ValidationError, FieldDatasetError, ValueError) as exc:
            raise FieldDatasetError(f"{source}:{line_no}: {exc}") from None
        if item["id"] in seen:
            raise FieldDatasetError(f"{source}:{line_no}: duplicate id {item['id']!r}")
        seen.add(item["id"])
        rows.append(item)
    if not rows:
        raise FieldDatasetError(f"no field items found in {source}")
    return rows
