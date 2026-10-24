"""Type-aware expected-value matching against whole-page extracted text.

This scores whether the supplied expected value appears in a page. It does not infer
field locations, labels, or semantic equivalence from document content.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_NUMBER = re.compile(r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s*%|\s*[A-Za-z][A-Za-z0-9./-]*)*(?![\w.])")
_NUMBER_BASE = re.compile(r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w.])")
_KNOWN_UNIT_PREFIXES = {
    "working", "business", "calendar", "day", "days", "week", "weeks", "month", "months", "year", "years",
    "hour", "hours", "minute", "minutes", "second", "seconds", "per", "percent", "%", "usd", "eur",
    "gbp", "jpy", "cad", "aud", "inr", "km", "kg", "g", "lb", "l", "ml", "m", "cm", "mm",
}
_CURRENCY = re.compile(
    r"(?<!\w)(?:(USD|EUR|GBP|JPY|CAD|AUD|INR)\s*)?([$€£¥₹])?\s*"
    r"([-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)\s*"
    r"(?:(USD|EUR|GBP|JPY|CAD|AUD|INR))?(?!\w)", re.IGNORECASE
)
_CURRENCY_CODES = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR"}
_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y", "%d-%m-%Y",
    "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y",
)
_DATE_CANDIDATE = re.compile(
    r"(?<!\w)(?:\d{1,4}[/-]\d{1,2}[/-]\d{1,4}|"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}|"
    r"\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})(?!\w)", re.IGNORECASE
)


def _text(value: Any) -> str:
    return " ".join(str(value).casefold().split())


def _parse_date(value: str) -> str | None:
    text = " ".join(value.strip().split())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _number(value: str) -> tuple[Decimal, str] | None:
    match = _NUMBER.fullmatch(value.strip())
    if not match:
        return None
    raw = match.group(0)
    numeric = re.match(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", raw)
    if not numeric:
        return None
    try:
        amount = Decimal(numeric.group(0).replace(",", ""))
    except InvalidOperation:
        return None
    unit = re.sub(r"\s+", " ", raw[numeric.end():].strip().casefold()).rstrip(".,;:")
    unit = unit.replace(" ", "")
    return amount.normalize(), unit


def _number_occurs(page_text: str, expected: tuple[Decimal, str]) -> bool:
    expected_amount, expected_unit = expected
    for match in _NUMBER_BASE.finditer(page_text):
        try:
            amount = Decimal(match.group(0).replace(",", "")).normalize()
        except InvalidOperation:
            continue
        if amount != expected_amount:
            continue
        suffix = page_text[match.end():]
        tokens = re.findall(r"%|[A-Za-z][A-Za-z0-9./-]*", suffix[:80])
        if expected_unit:
            joined = ""
            for token in tokens[:4]:
                joined += token.casefold().replace(" ", "").rstrip("./-")
                if joined == expected_unit:
                    return True
                if len(joined) > len(expected_unit):
                    break
            continue
        if not tokens or tokens[0].casefold() not in _KNOWN_UNIT_PREFIXES:
            return True
    return False


def _currency(value: str) -> tuple[str, Decimal] | None:
    m = _CURRENCY.fullmatch(value.strip())
    if not m:
        return None
    before_code, symbol, amount, after_code = m.groups()
    code = (before_code or after_code or _CURRENCY_CODES.get(symbol or "", "")).upper()
    if not code:
        return None
    try:
        return code, Decimal(amount.replace(",", "")).normalize()
    except InvalidOperation:
        return None


def normalize_value(value: Any, kind: str) -> Any:
    """Return a comparison key; units and currencies are normalized, never converted."""
    raw = str(value)
    if kind == "date":
        return _parse_date(raw)
    if kind == "currency":
        return _currency(raw)
    if kind == "number":
        return _number(raw)
    if kind in ("text", "table_cell"):
        return _text(raw)
    raise ValueError(f"unknown field kind {kind!r}")


def _boundary_pattern(expected: str) -> re.Pattern[str]:
    pieces = re.split(r"(\s+)", expected.strip())
    body = "".join(r"\s+" if p.isspace() else re.escape(p) for p in pieces)
    return re.compile(rf"(?<![\w]){body}(?![\w])", re.IGNORECASE)


def _exact_pattern(expected: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w]){re.escape(expected)}(?![\w])")


def _normalized_occurs(page_text: str, expected: Any, kind: str) -> bool:
    wanted = normalize_value(expected, kind)
    if wanted is None:
        return False
    if kind in ("text", "table_cell"):
        return bool(_boundary_pattern(str(expected)).search(page_text))
    if kind == "date":
        return any(_parse_date(m.group(0)) == wanted for m in _DATE_CANDIDATE.finditer(page_text))
    if kind == "currency":
        return any(_currency(m.group(0)) == wanted for m in _CURRENCY.finditer(page_text))
    if kind == "number":
        return _number_occurs(page_text, wanted)
    return False


def match_expected(page_text: str, expected: Any, kind: str) -> dict[str, bool]:
    """Exact boundary-aware presence and normalized presence in whole-page text."""
    exact = bool(_exact_pattern(str(expected)).search(page_text))
    return {"exact": exact, "normalized": _normalized_occurs(page_text, expected, kind)}
