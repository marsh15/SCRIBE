"""Pure extraction and pairing of changed values in aligned section text."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

ValueKind = Literal["number", "percentage", "currency", "date", "normative"]

_WS = re.compile(r"\s+")
_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-", "\u00a0": " ", "\u200b": "", "\ufeff": ""})


def _normalize(text: str) -> str:
    """Normalize comparison context locally without depending on scribe_eval."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text).translate(_TRANSLATE).lower()).strip()

_MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)"
_CURRENCY_CODE = r"(?:USD|EUR|GBP|CAD|AUD|NZD|JPY|CNY|CHF|INR|SEK|NOK|DKK)"
_SCALE = r"(?:\s+(?:thousand|million|billion))?"

_DATE_PATTERNS = (
    re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b"),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\.?\s+\d{{4}}\b", re.I),
    re.compile(rf"\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?[,]?\s+\d{{4}}\b", re.I),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\.?\b", re.I),
    re.compile(rf"\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b", re.I),
)
_CURRENCY_PATTERNS = (
    re.compile(rf"\b{_CURRENCY_CODE}\s*{_NUMBER}{_SCALE}\b", re.I),
    re.compile(rf"(?<!\w)(?:\$|€|£|¥)\s*{_NUMBER}{_SCALE}\b"),
    re.compile(rf"\b{_NUMBER}{_SCALE}\s*{_CURRENCY_CODE}\b", re.I),
    re.compile(rf"(?<![\w.]){_NUMBER}{_SCALE}\s*(?:\$|€|£|¥)(?!\w)"),
)
_PERCENT = re.compile(rf"(?<![\w.]){_NUMBER}\s*(?:%|percent\b|percentage\s+points?\b)", re.I)
_UNIT = (
    r"(?:(?:consecutive|calendar|working|business|additional|extra|unused|available|base-pay|base\s+pay)\s+){0,4}"
    r"(?:working\s+days?|calendar\s+days?|business\s+days?|days?|weeks?|months?|years?|"
    r"hours?|minutes?|seconds?|employees?|staff|people|loading\s+doors?|doors?|vehicles?|"
    r"km/h|m/s|mph|"
    r"kilomet(?:er|re)s?|miles?|meters?|lit(?:er|re)s?|items?|units?)"
)
_QUANTITY = re.compile(rf"(?<![\w.]){_NUMBER}\s+{_UNIT}\b", re.I)
_DEGREE_QUANTITY = re.compile(rf"(?<![\w.]){_NUMBER}\s*°[CF](?!\w)", re.I)
_NORMATIVE = re.compile(r"\b(?:shall|must|required|may\s+not|should)\b", re.I)


@dataclass(frozen=True)
class ExtractedValue:
    kind: ValueKind
    value: str
    start: int
    end: int
    context: str
    context_position: int = 0


@dataclass(frozen=True)
class ValueChange:
    kind: ValueKind
    old_value: str | None
    new_value: str | None
    context: str
    old_start: int | None
    old_end: int | None
    new_start: int | None
    new_end: int | None


def _sentence_span(text: str, position: int) -> tuple[int, int]:
    for start, end in _sentence_spans(text):
        if start <= position < end:
            return start, end
    return 0, len(text)


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Split on prose punctuation and line breaks, keeping decimals and clause numbers intact."""
    spans: list[tuple[int, int]] = []
    start = 0
    index = 0

    def append(left: int, right: int) -> None:
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if left < right:
            spans.append((left, right))

    while index < len(text):
        char = text[index]
        if char == "\n":
            append(start, index)
            index += 1
            while index < len(text) and text[index].isspace():
                index += 1
            start = index
            continue
        if char not in ".!?":
            index += 1
            continue
        if char == "." and index > 0 and index + 1 < len(text) and text[index - 1].isdigit() and text[index + 1].isdigit():
            index += 1
            continue
        if index + 1 < len(text) and not text[index + 1].isspace():
            index += 1
            continue
        line_start = text.rfind("\n", start, index) + 1
        prefix = text[line_start : index + 1].strip()
        if char == "." and re.fullmatch(r"\d+(?:\.\d+)*\.?", prefix):
            index += 1
            continue
        end = index + 1
        while end < len(text) and text[end] in "\"')]}”’":
            end += 1
        append(start, end)
        start = end
        while start < len(text) and text[start].isspace():
            start += 1
        index = start
    append(start, len(text))
    return spans


def _context_template(text: str, start: int, end: int, values: list[tuple[int, int]]) -> str:
    local = [(left, right) for left, right in values if start <= left < right <= end]
    pieces: list[str] = []
    cursor = start
    for ordinal, (left, right) in enumerate(sorted(local), start=1):
        if left < cursor:
            continue
        pieces.append(text[cursor:left])
        pieces.append(f" <value {ordinal}> ")
        cursor = right
    pieces.append(text[cursor:end])
    return _normalize("".join(pieces))


def _collect_non_overlapping(text: str) -> list[tuple[ValueKind, int, int]]:
    matches: list[tuple[int, int, int, ValueKind]] = []
    priority = {"date": 0, "currency": 1, "percentage": 2, "number": 3}
    for pattern in _DATE_PATTERNS:
        matches.extend((match.start(), match.end(), priority["date"], "date") for match in pattern.finditer(text))
    for pattern in _CURRENCY_PATTERNS:
        matches.extend((match.start(), match.end(), priority["currency"], "currency") for match in pattern.finditer(text))
    matches.extend((match.start(), match.end(), priority["percentage"], "percentage") for match in _PERCENT.finditer(text))
    for match in _QUANTITY.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        number_token = match.group().split(maxsplit=1)[0]
        line_prefix = text[line_start : match.start()]
        after_number = match.group()[len(number_token):].lstrip()
        if (
            not line_prefix.strip()
            and re.fullmatch(r"\d+(?:\.\d+)+", number_token)
            and after_number[:1].isupper()
        ):
            # A numbered clause such as "2.5 Employees ..." is a heading label,
            # not a quantity with a unit.
            continue
        matches.append((match.start(), match.end(), priority["number"], "number"))
    matches.extend(
        (match.start(), match.end(), priority["number"], "number")
        for match in _DEGREE_QUANTITY.finditer(text)
    )

    accepted: list[tuple[ValueKind, int, int]] = []
    occupied: list[tuple[int, int]] = []
    for start, end, _, kind in sorted(matches, key=lambda item: (item[0], item[2], -(item[1] - item[0]), item[1])):
        if any(start < used_end and used_start < end for used_start, used_end in occupied):
            continue
        accepted.append((kind, start, end))  # type: ignore[arg-type]
        occupied.append((start, end))
    return sorted(accepted, key=lambda item: (item[1], item[2]))


def extract_values(text: str) -> list[ExtractedValue]:
    """Return dates, currencies, percentages, quantities, and normative sentences."""
    extracted: list[tuple[ValueKind, int, int]] = _collect_non_overlapping(text)
    data_spans = [(start, end) for _, start, end in extracted]
    normative_spans: list[tuple[int, int]] = []
    for start, end in _sentence_spans(text):
        if _NORMATIVE.search(text[start:end]):
            normative_spans.append((start, end))

    values: list[ExtractedValue] = []
    for kind, start, end in extracted:
        sentence_start, sentence_end = _sentence_span(text, start)
        context = _context_template(text, sentence_start, sentence_end, data_spans)
        context_position = sum(1 for left, _ in data_spans if sentence_start <= left < start)
        values.append(ExtractedValue(kind, text[start:end], start, end, context, context_position))
    for start, end in normative_spans:
        sentence_start, sentence_end = _sentence_span(text, start)
        context = _context_template(text, sentence_start, sentence_end, data_spans)
        values.append(ExtractedValue("normative", text[start:end], start, end, context))
    return sorted(values, key=lambda value: (value.start, value.end, value.kind))


def _relative_position_gap(old: int, new: int, old_count: int, new_count: int) -> float:
    return abs(old / max(old_count - 1, 1) - new / max(new_count - 1, 1))


def pair_value_changes(old_text: str, new_text: str, *, context_threshold: float = 0.35) -> list[ValueChange]:
    """Pair same-kind values by sentence context and relative position.

    Unchanged pairs are omitted. Values with no plausible counterpart are kept as
    additions or removals, so a changed section can also report dropped rules.
    """
    if not 0 <= context_threshold <= 1:
        raise ValueError("context_threshold must be in [0, 1]")
    old_values = extract_values(old_text)
    new_values = extract_values(new_text)
    kinds: tuple[ValueKind, ...] = ("date", "currency", "percentage", "number", "normative")
    changes: list[ValueChange] = []

    for kind in kinds:
        old_group = [value for value in old_values if value.kind == kind]
        new_group = [value for value in new_values if value.kind == kind]
        pairs: list[tuple[float, int, float, int, int]] = []
        for old_index, old in enumerate(old_group):
            for new_index, new in enumerate(new_group):
                similarity = SequenceMatcher(None, old.context, new.context, autojunk=False).ratio()
                if similarity >= context_threshold:
                    pairs.append((similarity, abs(old.context_position - new.context_position), _relative_position_gap(old_index, new_index, len(old_group), len(new_group)), old_index, new_index))
        pairs.sort(key=lambda pair: (-pair[0], pair[1], pair[2], pair[3], pair[4]))
        matched_old: set[int] = set()
        matched_new: set[int] = set()
        for _, _, _, old_index, new_index in pairs:
            if old_index in matched_old or new_index in matched_new:
                continue
            old, new = old_group[old_index], new_group[new_index]
            matched_old.add(old_index)
            matched_new.add(new_index)
            if _normalize(old.value) == _normalize(new.value):
                continue
            changes.append(ValueChange(kind, old.value, new.value, old.context or new.context,
                                       old.start, old.end, new.start, new.end))
        for old_index, old in enumerate(old_group):
            if old_index not in matched_old:
                changes.append(ValueChange(kind, old.value, None, old.context, old.start, old.end, None, None))
        for new_index, new in enumerate(new_group):
            if new_index not in matched_new:
                changes.append(ValueChange(kind, None, new.value, new.context, None, None, new.start, new.end))

    return sorted(changes, key=lambda change: (
        change.old_start if change.old_start is not None else (change.new_start or 0),
        change.new_start if change.new_start is not None else -1,
        change.kind,
    ))
