"""Items file access: ordering, resume position, and atomic, byte-preserving writes.

Every decision rewrites the items file through ``atomic_write_text`` (temp file in the same directory, fsync,
``os.replace``). Untouched lines are kept verbatim (same bytes, same line order, same key order); only the decided
item's line changes, or is removed (drop) and appended to ``dropped.jsonl`` next to it.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .kinds import KindAdapter
from .source import Corpus

_ID_RE = re.compile(r"\b[a-z]{2,}-\d+\b")
_RANGE_RE = re.compile(r"\b([a-z]{2,})-(\d+)\.\.(?:([a-z]{2,})-)?(\d+)\b")
DEFAULT_ORDER_FILES = ("REVIEW_FIRST.md", "UNREVIEWED_T28.md")


class ReviewError(Exception):
    """A refused decision. ``errors`` are shown to the reviewer; nothing was written."""

    def __init__(self, errors: list[str] | str, status: int = 422):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        self.status = status
        super().__init__("; ".join(self.errors))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` so a reader sees either the old or the new file, never a partial one."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    try:  # make the rename itself durable (best effort; not supported everywhere)
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def dumps(raw: dict[str, Any]) -> str:
    return json.dumps(raw, ensure_ascii=False)


# ----------------------------------------------------------------------------------------------- ordering

def ids_from_markdown(path: Path) -> list[str]:
    """Case ids named in the first column of the tables of a REVIEW_FIRST-style markdown file, in order.

    Handles ``ua-11, ua-12`` and ranges such as ``tb-13..tb-16`` (or ``tb-13..16``).
    """
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cell = line.strip().strip("|").split("|", 1)[0]
        for m in _RANGE_RE.finditer(cell):
            prefix, lo, prefix2, hi = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
            if prefix2 in (None, prefix) and lo <= hi:
                width = len(m.group(2))
                out += [f"{prefix}-{n:0{width}d}" for n in range(lo, hi + 1)]
        out += _ID_RE.findall(_RANGE_RE.sub(" ", cell))
    return out


def default_order(items_path: Path) -> list[str]:
    """For a dataset directory holding REVIEW_FIRST.md / UNREVIEWED_T28.md: the ids those files name, in that order."""
    ids: list[str] = []
    for name in DEFAULT_ORDER_FILES:
        p = items_path.parent / name
        if p.is_file():
            ids += ids_from_markdown(p)
    return ids


def ids_from_order_file(path: Path) -> list[str]:
    """Ids from a plain file: any whitespace/comma separated tokens; ``#`` starts a comment line."""
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        out += [t for t in re.split(r"[\s,]+", line.strip()) if t]
    return out


# ----------------------------------------------------------------------------------------------- store

class ItemStore:
    def __init__(self, items_path: str | Path, adapter: KindAdapter, corpus: Corpus, reviewer: str,
                 order: list[str] | None = None, clock: Callable[[], str] = now_iso):
        self.path = Path(items_path)
        self.dropped_path = self.path.with_name("dropped.jsonl")
        self.adapter = adapter
        self.corpus = corpus
        self.reviewer = reviewer
        self.order = list(order or [])
        self.clock = clock
        self._lock = threading.RLock()
        if not self.path.is_file():
            raise ReviewError(f"items file not found: {self.path}", 404)

    # -- reading (always from disk: the file is the source of truth) --
    def _read(self) -> tuple[list[str], list[dict[str, Any] | None]]:
        lines = self.path.read_bytes().decode("utf-8").split("\n")
        parsed: list[dict[str, Any] | None] = []
        for ln in lines:
            try:
                obj = json.loads(ln) if ln.strip() else None
            except json.JSONDecodeError:
                obj = None
            parsed.append(obj if isinstance(obj, dict) else None)
        return lines, parsed

    def items(self) -> list[dict[str, Any]]:
        return [p for p in self._read()[1] if p is not None]

    def dropped_count(self) -> int:
        if not self.dropped_path.is_file():
            return 0
        return sum(1 for ln in self.dropped_path.read_text(encoding="utf-8").split("\n") if ln.strip())

    def sequence(self, items: list[dict[str, Any]] | None = None) -> list[str]:
        items = self.items() if items is None else items
        ids = [str(i.get("id")) for i in items]
        present = set(ids)
        first: list[str] = []
        for i in self.order:
            if i in present and i not in first:
                first.append(i)
        seen = set(first)
        return first + [i for i in ids if i not in seen]

    @staticmethod
    def is_reviewed(item: dict[str, Any]) -> bool:
        return item.get("reviewed") is True

    def get(self, item_id: str) -> dict[str, Any]:
        for it in self.items():
            if str(it.get("id")) == item_id:
                return it
        raise ReviewError(f"no item {item_id!r} (already dropped?)", 404)

    def state(self) -> dict[str, Any]:
        items = self.items()
        by_id = {str(i.get("id")): i for i in items}
        seq = self.sequence(items)
        reviewed = sum(1 for i in items if self.is_reviewed(i))
        current = next((i for i in seq if not self.is_reviewed(by_id[i])), None)
        return {
            "total": len(items), "reviewed": reviewed, "dropped": self.dropped_count(),
            "remaining": len(items) - reviewed, "current": current,
            "sequence": [{"id": i, "reviewed": self.is_reviewed(by_id[i])} for i in seq],
        }

    def nav(self, from_id: str | None, direction: str = "next", pending_only: bool = True) -> str | None:
        """Next/previous id in review order after ``from_id`` (wrapping). ``pending_only`` skips reviewed items."""
        items = self.items()
        by_id = {str(i.get("id")): i for i in items}
        seq = self.sequence(items)
        if not seq:
            return None
        step = 1 if direction == "next" else -1
        start = seq.index(from_id) if from_id in seq else (-1 if step == 1 else 0)
        for k in range(1, len(seq) + 1):
            cand = seq[(start + step * k) % len(seq)]
            if not pending_only or not self.is_reviewed(by_id[cand]):
                return cand
        return None

    # -- decisions --
    def _stamp(self, raw: dict[str, Any], note: str | None) -> dict[str, Any]:
        new = dict(raw)
        new["reviewed"] = True
        new["reviewed_by"] = self.reviewer
        new["reviewed_at"] = self.clock()
        if note and note.strip():
            new["review_note"] = note.strip()
        return new

    def _replace(self, item_id: str, make: Callable[[dict[str, Any]], dict[str, Any] | None]) -> dict[str, Any]:
        """Apply ``make`` to the item's line (None removes it) and write the file atomically. Returns the old item."""
        with self._lock:
            lines, parsed = self._read()
            idx = [i for i, p in enumerate(parsed) if p is not None and str(p.get("id")) == item_id]
            if not idx:
                raise ReviewError(f"no item {item_id!r} (already dropped?)", 404)
            if len(idx) > 1:
                raise ReviewError(f"id {item_id!r} occurs {len(idx)} times in {self.path.name}; fix the file first", 409)
            i = idx[0]
            old = parsed[i]
            assert old is not None
            new = make(old)
            if new is None:
                del lines[i]
            else:
                errs = self.adapter.validate(new, self.corpus)
                if errs:
                    raise ReviewError(errs)
                lines[i] = dumps(new)
            atomic_write_text(self.path, "\n".join(lines))
            return old

    def accept(self, item_id: str, note: str | None = None) -> None:
        self._replace(item_id, lambda old: self._stamp(old, note))

    def edit(self, item_id: str, values: dict[str, Any], note: str | None = None) -> None:
        def make(old: dict[str, Any]) -> dict[str, Any]:
            try:
                edited = self.adapter.apply_edit(old, values)
            except ValueError as exc:
                raise ReviewError(str(exc)) from None
            return self._stamp(edited, note)

        self._replace(item_id, make)

    def drop(self, item_id: str, reason: str) -> None:
        reason = " ".join((reason or "").split())
        if not reason:
            raise ReviewError("a drop needs a one-line reason")
        with self._lock:

            def remove(old: dict[str, Any]) -> None:
                # Written first: if removing from the items file then fails, the item is in both files, never in neither.
                rec = dict(old)
                rec.update(dropped_by=self.reviewer, dropped_at=self.clock(), drop_reason=reason)
                keep: list[str] = []
                if self.dropped_path.is_file():
                    for ln in self.dropped_path.read_text(encoding="utf-8").split("\n"):
                        if not ln.strip():
                            continue
                        try:
                            if str(json.loads(ln).get("id")) == item_id:
                                continue  # re-drop after a half-finished drop: replace, do not duplicate
                        except (json.JSONDecodeError, AttributeError):
                            pass
                        keep.append(ln)
                atomic_write_text(self.dropped_path, "".join(ln + "\n" for ln in [*keep, dumps(rec)]))
                return None

            self._replace(item_id, remove)
