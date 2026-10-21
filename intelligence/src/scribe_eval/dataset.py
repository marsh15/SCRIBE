"""Golden dataset case schema and cases.jsonl loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Split = Literal["dev", "validation", "test"]
SPLITS: tuple[str, ...] = ("dev", "validation", "test")


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc: str
    version: int | str
    quote: str = Field(max_length=200)
    page: int | None = None
    grade: Literal[1, 2]


class Forbidden(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc: str


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    split: Split
    category: str
    tags: list[str] = []
    question: str
    answerable: bool
    reference_answer: str
    must_include: list[str] = []
    evidence: list[Evidence] = []
    forbidden: list[Forbidden] = []
    # T28: provenance. Cases drafted by a model carry origin="machine-drafted-t28" and reviewed=false until a person
    # has checked the question, reference answer, quotes and forbidden docs. Absent on the original T05 cases.
    origin: str | None = None
    reviewed: bool | None = None
    # A1: set by `scribe-eval review` when a person accepts or edits the case. Optional, so older files parse unchanged.
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None


def parse_cases(path: Path) -> tuple[list[Case], list[str]]:
    """Return (valid cases, error messages) from a cases.jsonl file."""
    cases: list[Case] = []
    errors: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            cases.append(Case.model_validate(json.loads(line)))
        except json.JSONDecodeError as exc:
            errors.append(f"line {lineno}: invalid JSON: {exc}")
        except ValidationError as exc:
            parts = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            errors.append(f"line {lineno}: schema error: {parts}")
    return cases, errors


def load_cases(path: Path) -> list[Case]:
    cases, errors = parse_cases(path)
    if errors:
        raise ValueError("\n".join(errors))
    return cases
