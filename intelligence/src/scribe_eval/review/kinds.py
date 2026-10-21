"""Item kinds the review tool understands. One small adapter per kind: load, validate, evidence locations, editable
fields, and how an edit is applied. ``case`` reuses ``scribe_eval.dataset.Case``; ``field`` (parsing fields) and
``change`` (version-change labels) are defined here and may be extended by later tasks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from ..dataset import Case
from .source import Corpus

REVIEW_KEYS = ("reviewed", "reviewed_by", "reviewed_at", "review_note")
JSON_EDITOR_FIELD = "__json__"


class _Reviewable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewed: bool | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None


class FieldItem(_Reviewable):
    """A parsing ground-truth field: what a parser must extract from one page of one document."""

    id: str
    doc: str
    page: int | None = None
    field_id: str
    kind: Literal["text", "number", "date", "currency", "table_cell"]
    expected: str | int | float
    quote: str | None = None


class ChangeItem(_Reviewable):
    """A version-change label: how a section differs between two versions of a document."""

    id: str
    pair_id: str
    old_doc: str
    new_doc: str
    section: str
    change_type: Literal["added", "removed", "changed", "moved"]
    old_value: str | None = None
    new_value: str | None = None
    old_page: int | None = None
    new_page: int | None = None
    quote_old: str | None = None
    quote_new: str | None = None


@dataclass
class Location:
    """One piece of evidence to show: a document page and the text to highlight on it."""

    label: str
    doc: str
    page: int | None
    needle: str
    needle_label: str = "quote"
    meta: str = ""


def schema_errors(model: type[BaseModel], raw: dict[str, Any]) -> list[str]:
    try:
        model.model_validate(raw)
    except ValidationError as exc:
        return [f"{'.'.join(map(str, e['loc'])) or '(item)'}: {e['msg']}" for e in exc.errors()]
    return []


class KindAdapter:
    kind: str
    model: type[BaseModel]

    def load(self, raw: dict[str, Any]) -> BaseModel:
        return self.model.model_validate(raw)

    def validate(self, raw: dict[str, Any], corpus: Corpus) -> list[str]:
        raise NotImplementedError

    def locations(self, raw: dict[str, Any]) -> list[Location]:
        raise NotImplementedError

    def editor(self) -> dict[str, Any]:
        """Describes the edit UI: {"mode": "form"|"json", "fields": [...]}."""
        raise NotImplementedError

    def apply_edit(self, raw: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        """Return a new raw item with ``values`` applied. Raises ValueError with a readable message."""
        raise NotImplementedError


def _page(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"page must be a whole number or empty, got {value!r}") from None


class CaseAdapter(KindAdapter):
    kind = "case"
    model = Case

    def validate(self, raw: dict[str, Any], corpus: Corpus) -> list[str]:
        errs = schema_errors(Case, raw)
        if errs:
            return errs
        c = Case.model_validate(raw)
        if c.answerable and not c.evidence:
            errs.append("answerable case has no evidence")
        if not c.answerable and c.evidence:
            errs.append("unanswerable case must have empty evidence")
        for i, ev in enumerate(c.evidence):
            tag = f"evidence[{i}] ({ev.doc})"
            if not corpus.has(ev.doc):
                errs.append(f"{tag}: document not found in corpus")
                continue
            n = corpus.quote_count(ev.doc, ev.quote)
            if n is None:
                errs.append(f"{tag}: quote is empty or the document cannot be read")
            elif n != 1:
                errs.append(f"{tag}: quote found {n} times in the document (must be exactly once)")
        for fb in c.forbidden:
            if not corpus.has(fb.doc):
                errs.append(f"forbidden doc not found in corpus: {fb.doc}")
        return errs

    def locations(self, raw: dict[str, Any]) -> list[Location]:
        out = []
        for i, ev in enumerate(raw.get("evidence") or []):
            if not isinstance(ev, dict):
                continue
            out.append(Location(
                label=f"evidence[{i}]", doc=str(ev.get("doc", "")), page=_safe_page(ev.get("page")),
                needle=str(ev.get("quote", "")),
                meta=f"grade {ev.get('grade')}, version {ev.get('version')}",
            ))
        return out

    def editor(self) -> dict[str, Any]:
        return {"mode": "form", "fields": [
            {"name": "question", "type": "textarea"},
            {"name": "reference_answer", "type": "textarea"},
            {"name": "must_include", "type": "lines", "help": "one string per line"},
            {"name": "answerable", "type": "bool"},
            {"name": "category", "type": "text"},
            {"name": "tags", "type": "csv", "help": "comma separated"},
            {"name": "evidence", "type": "evidence"},
            {"name": "forbidden", "type": "lines", "help": "one document per line"},
        ]}

    def apply_edit(self, raw: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        new = dict(raw)
        for key in ("question", "reference_answer", "category"):
            if key in values:
                new[key] = _str(values[key], key)
        if "answerable" in values:
            if not isinstance(values["answerable"], bool):
                raise ValueError("answerable must be true or false")
            new["answerable"] = values["answerable"]
        for key in ("must_include", "tags"):
            if key in values:
                new[key] = _str_list(values[key], key)
        if "forbidden" in values:
            new["forbidden"] = [{"doc": d} for d in _str_list(values["forbidden"], "forbidden")]
        if "evidence" in values:
            if not isinstance(values["evidence"], list):
                raise ValueError("evidence must be a list")
            ev_out = []
            for i, ev in enumerate(values["evidence"]):
                if not isinstance(ev, dict):
                    raise ValueError(f"evidence[{i}] must be an object")
                version = ev.get("version")
                if isinstance(version, str) and version.strip().isdigit():
                    version = int(version.strip())
                grade = ev.get("grade")
                try:
                    grade = int(grade)
                except (TypeError, ValueError):
                    raise ValueError(f"evidence[{i}].grade must be 1 or 2") from None
                try:
                    page = _page(ev.get("page"))
                except ValueError as exc:
                    raise ValueError(f"evidence[{i}]: {exc}") from None
                ev_out.append({"doc": _str(ev.get("doc", ""), "doc"), "version": version,
                               "quote": _str(ev.get("quote", ""), "quote"), "page": page, "grade": grade})
            new["evidence"] = ev_out
        return new


def _safe_page(value: Any) -> int | None:
    try:
        return _page(value)
    except ValueError:
        return None


def _str(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    return value


def _str_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{name} must be a list of text values")
    return [v.strip() for v in value if v.strip()]


class _JsonAdapter(KindAdapter):
    """Field and change items are edited as one JSON object, validated on save."""

    def editor(self) -> dict[str, Any]:
        return {"mode": "json", "fields": [{"name": JSON_EDITOR_FIELD, "type": "json"}]}

    def apply_edit(self, raw: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        text = values.get(JSON_EDITOR_FIELD)
        if not isinstance(text, str):
            raise ValueError("expected the item as JSON text")
        try:
            new = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc}") from None
        if not isinstance(new, dict):
            raise ValueError("the item must be a JSON object")
        if new.get("id") != raw.get("id"):
            raise ValueError(f"the id must stay {raw.get('id')!r}")
        return new

    def _doc_errors(self, corpus: Corpus, *docs: tuple[str, Any]) -> list[str]:
        return [f"{name}: document not found in corpus: {d}" for name, d in docs if d and not corpus.has(str(d))]


class FieldAdapter(_JsonAdapter):
    kind = "field"
    model = FieldItem

    def validate(self, raw: dict[str, Any], corpus: Corpus) -> list[str]:
        errs = schema_errors(FieldItem, raw)
        return errs or self._doc_errors(corpus, ("doc", raw.get("doc")))

    def locations(self, raw: dict[str, Any]) -> list[Location]:
        quote, expected = raw.get("quote"), raw.get("expected")
        needle, label = (str(quote), "quote") if quote else ("" if expected is None else str(expected), "expected value")
        return [Location("field", str(raw.get("doc", "")), _safe_page(raw.get("page")), needle, label,
                         f"{raw.get('kind')}: {raw.get('field_id')}")]


class ChangeAdapter(_JsonAdapter):
    kind = "change"
    model = ChangeItem

    def validate(self, raw: dict[str, Any], corpus: Corpus) -> list[str]:
        errs = schema_errors(ChangeItem, raw)
        return errs or self._doc_errors(corpus, ("old_doc", raw.get("old_doc")), ("new_doc", raw.get("new_doc")))

    def locations(self, raw: dict[str, Any]) -> list[Location]:
        out = []
        for side in ("old", "new"):
            quote, value = raw.get(f"quote_{side}"), raw.get(f"{side}_value")
            needle, label = (str(quote), "quote") if quote else ("" if value is None else str(value), "value")
            out.append(Location(side, str(raw.get(f"{side}_doc", "")), _safe_page(raw.get(f"{side}_page")), needle, label,
                                f"{raw.get('change_type')} in section {raw.get('section')!r}"))
        return out


ADAPTERS: dict[str, KindAdapter] = {a.kind: a for a in (CaseAdapter(), FieldAdapter(), ChangeAdapter())}
KINDS: tuple[str, ...] = tuple(ADAPTERS)
