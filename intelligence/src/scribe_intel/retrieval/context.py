"""Pure context selection and extractive compression helpers."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Literal, Sequence

ContextStrategy = Literal["legacy", "top20_fused", "top5_reranked", "top5_parent", "compressed"]

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_PARAGRAPH_BREAK = re.compile(r"\r?\n(?:[ \t]*\r?\n)+[ \t]*")


@dataclass(frozen=True)
class CharacterSpan:
    """A half-open character range in the original candidate text."""

    start: int
    end: int


@dataclass(frozen=True)
class CompressedText:
    text: str
    kept_spans: tuple[CharacterSpan, ...]
    dropped_spans: tuple[CharacterSpan, ...]


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return set(_WORD.findall(normalized))


def _sentence_spans(text: str) -> list[CharacterSpan]:
    """Split at sentence punctuation and blank-line paragraph boundaries.

    A single newline is treated as a soft wrap and stays inside its sentence.
    """
    spans: list[CharacterSpan] = []
    start = 0
    i = 0

    def append(left: int, right: int) -> None:
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if left < right:
            spans.append(CharacterSpan(left, right))

    while i < len(text):
        char = text[i]
        if char in "\r\n":
            paragraph = _PARAGRAPH_BREAK.match(text, i)
            if paragraph is None:
                i += 1
                continue
            append(start, i)
            i = paragraph.end()
            start = i
            continue
        if char not in ".!?":
            i += 1
            continue
        if char == "." and i > 0 and i + 1 < len(text) and text[i - 1].isdigit() and text[i + 1].isdigit():
            i += 1
            continue
        end = i + 1
        while end < len(text) and text[end] in "\"')]}”’":
            end += 1
        if end < len(text) and not text[end].isspace():
            i += 1
            continue
        append(start, end)
        start = end
        while start < len(text) and text[start].isspace():
            start += 1
        i = start
    append(start, len(text))
    return spans


def _score(sentence: str, query_tokens: set[str]) -> float:
    sentence_tokens = _tokens(sentence)
    if not sentence_tokens or not query_tokens:
        return 0.0
    overlap = len(sentence_tokens & query_tokens)
    return overlap / len(sentence_tokens | query_tokens)


def _complement(length: int, kept: Sequence[CharacterSpan]) -> tuple[CharacterSpan, ...]:
    dropped: list[CharacterSpan] = []
    cursor = 0
    for span in sorted(kept, key=lambda item: (item.start, item.end)):
        if cursor < span.start:
            dropped.append(CharacterSpan(cursor, span.start))
        cursor = max(cursor, span.end)
    if cursor < length:
        dropped.append(CharacterSpan(cursor, length))
    return tuple(dropped)


def compress_text(
    text: str,
    query: str,
    *,
    budget_tokens: int,
    count_tokens: Callable[[str], int],
) -> CompressedText:
    """Keep the most query-relevant whole sentences within a token budget.

    Returned spans are half-open offsets into ``text``. Selected text is emitted in
    original order, while sentence ranking breaks ties by original position.
    """
    if budget_tokens < 0:
        raise ValueError("budget_tokens must be non-negative")
    sentences = _sentence_spans(text)
    query_tokens = _tokens(query)
    ranked = sorted(
        enumerate(sentences),
        key=lambda pair: (-_score(text[pair[1].start:pair[1].end], query_tokens), pair[0]),
    )
    kept: list[CharacterSpan] = []
    for _, span in ranked:
        tentative = sorted((*kept, span), key=lambda item: (item.start, item.end))
        rendered = "\n\n".join(text[item.start:item.end] for item in tentative)
        if count_tokens(rendered) <= budget_tokens:
            kept = tentative
    kept.sort(key=lambda span: (span.start, span.end))
    if len(kept) == len(sentences) and count_tokens(text) <= budget_tokens:
        output = text
        kept_spans = (CharacterSpan(0, len(text)),) if text else ()
    else:
        output = "\n\n".join(text[span.start:span.end] for span in kept)
        kept_spans = tuple(kept)
    return CompressedText(output, kept_spans, _complement(len(text), kept_spans))
