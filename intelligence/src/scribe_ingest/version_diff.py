"""Deterministic alignment of child chunks from two document versions."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Literal, Sequence
import unicodedata

from .chunking import Chunk

ChangeKind = Literal["added", "removed", "changed", "moved", "unchanged"]

_WS = re.compile(r"\s+")
_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-", "\u00a0": " ", "\u200b": "", "\ufeff": ""})


def _normalize(text: str) -> str:
    """Normalize headings and text locally without importing the eval package."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text).translate(_TRANSLATE).lower()).strip()


@dataclass(frozen=True)
class SectionChange:
    """One old-to-new child-chunk alignment. IDs are the chunk indexes from the chunker."""

    kind: ChangeKind
    old_id: int | None
    new_id: int | None
    old_path: tuple[str, ...] | None
    new_path: tuple[str, ...] | None
    old_text: str | None
    new_text: str | None
    similarity: float | None = None


def _path_key(chunk: Chunk) -> tuple[str, ...]:
    return tuple(_normalize(part) for part in chunk.heading_path)


def _text_key(chunk: Chunk) -> str:
    return _normalize(chunk.content)


def _change(
    kind: ChangeKind,
    old: Chunk | None,
    new: Chunk | None,
    *,
    similarity: float | None = None,
) -> SectionChange:
    return SectionChange(
        kind=kind,
        old_id=old.chunk_index if old is not None else None,
        new_id=new.chunk_index if new is not None else None,
        old_path=tuple(old.heading_path) if old is not None else None,
        new_path=tuple(new.heading_path) if new is not None else None,
        old_text=old.content if old is not None else None,
        new_text=new.content if new is not None else None,
        similarity=similarity,
    )


def _relative_position_gap(old_index: int, new_index: int, old_count: int, new_count: int) -> float:
    old_position = old_index / max(old_count - 1, 1)
    new_position = new_index / max(new_count - 1, 1)
    return abs(old_position - new_position)


def align_sections(
    old_sections: Sequence[Chunk],
    new_sections: Sequence[Chunk],
    *,
    similarity_threshold: float = 0.6,
) -> list[SectionChange]:
    """Align chunker-produced child chunks by normalized path, hash, then text similarity.

    Matching is one-to-one. Duplicate paths and hashes are paired in document order,
    so identical repeated passages stay deterministic and keep their original IDs.
    """
    if not 0 <= similarity_threshold <= 1:
        raise ValueError("similarity_threshold must be in [0, 1]")

    old_matched: dict[int, tuple[int, ChangeKind, float | None]] = {}
    new_matched: set[int] = set()

    # 1. A section with the same normalized path keeps its identity. Text changes
    # at that path are reported as changes.
    new_by_path: dict[tuple[str, ...], list[int]] = {}
    for new_index, section in enumerate(new_sections):
        new_by_path.setdefault(_path_key(section), []).append(new_index)
    path_offsets: dict[tuple[str, ...], int] = {}
    for old_index, old in enumerate(old_sections):
        key = _path_key(old)
        candidates = new_by_path.get(key, [])
        offset = path_offsets.get(key, 0)
        if offset >= len(candidates):
            continue
        new_index = candidates[offset]
        path_offsets[key] = offset + 1
        new = new_sections[new_index]
        kind: ChangeKind = "unchanged" if _text_key(old) == _text_key(new) else "changed"
        old_matched[old_index] = (new_index, kind, 1.0 if kind == "unchanged" else None)
        new_matched.add(new_index)

    # 2. Equal normalized content at another path is a move.
    new_by_hash: dict[str, list[int]] = {}
    for new_index, section in enumerate(new_sections):
        if new_index not in new_matched:
            new_by_hash.setdefault(section.content_hash, []).append(new_index)
    hash_offsets: dict[str, int] = {}
    for old_index, old in enumerate(old_sections):
        if old_index in old_matched:
            continue
        candidates = new_by_hash.get(old.content_hash, [])
        offset = hash_offsets.get(old.content_hash, 0)
        if offset >= len(candidates):
            continue
        new_index = candidates[offset]
        hash_offsets[old.content_hash] = offset + 1
        old_matched[old_index] = (new_index, "moved", 1.0)
        new_matched.add(new_index)

    # 3. Similar remaining text is a changed section whose heading path changed.
    # Rank all eligible pairs by text similarity, then relative document position.
    old_remaining = [i for i in range(len(old_sections)) if i not in old_matched]
    new_remaining = [i for i in range(len(new_sections)) if i not in new_matched]
    candidates: list[tuple[float, float, int, int]] = []
    for old_index in old_remaining:
        old_text = _text_key(old_sections[old_index])
        if not old_text:
            continue
        for new_index in new_remaining:
            new_text = _text_key(new_sections[new_index])
            if not new_text:
                continue
            similarity = SequenceMatcher(None, old_text, new_text, autojunk=False).ratio()
            if similarity >= similarity_threshold:
                candidates.append((similarity, _relative_position_gap(old_index, new_index, len(old_sections), len(new_sections)), old_index, new_index))

    candidates.sort(key=lambda candidate: (-candidate[0], candidate[1], candidate[2], candidate[3]))
    for similarity, _, old_index, new_index in candidates:
        if old_index in old_matched or new_index in new_matched:
            continue
        old_matched[old_index] = (new_index, "changed", similarity)
        new_matched.add(new_index)

    # Emit matches and additions in new-document order, then removals in old order.
    output: list[SectionChange] = []
    for new_index, new in enumerate(new_sections):
        old_index = next((i for i, (matched_new, _, _) in old_matched.items() if matched_new == new_index), None)
        if old_index is None:
            output.append(_change("added", None, new))
            continue
        kind, similarity = old_matched[old_index][1:]
        output.append(_change(kind, old_sections[old_index], new, similarity=similarity))
    for old_index, old in enumerate(old_sections):
        if old_index not in old_matched:
            output.append(_change("removed", old, None))
    return output
