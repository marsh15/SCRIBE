"""Disk cache for embedding and rerank responses (DESIGN D18). No provider calls here.

sqlite3 (stdlib) key/value store. Keys:
  * embeddings: sha256 of (model, dims, task, sha256(text))  -> ``embedding_key``
  * rerank:     sha256 of (model, query, [doc hashes])        -> ``rerank_key``

``SCRIBE_CACHE_MODE``: unset/``record`` computes and stores on a miss;
``replay`` (CI) never computes and raises ``CacheMiss`` instead.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

MODES = ("record", "replay")


class CacheMiss(RuntimeError):
    pass


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embedding_key(model: str, dims: int, task: str, text: str) -> str:
    return "emb:" + _sha([model, dims, task, text_hash(text)])


def rerank_key(model: str, query: str, doc_hashes: Sequence[str]) -> str:
    return "rr:" + _sha([model, query, list(doc_hashes)])


class DiskCache:
    def __init__(self, path: str | Path, mode: str | None = None, concurrency: int = 4):
        mode = mode or os.environ.get("SCRIBE_CACHE_MODE") or "record"
        if mode not in MODES:
            raise ValueError(f"SCRIBE_CACHE_MODE must be one of {MODES}, got {mode!r}")
        self.mode = mode
        self.hits = 0
        self.misses = 0
        self._sem = asyncio.Semaphore(concurrency)
        self._lock = threading.Lock()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._db.commit()

    @property
    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses}

    def _get(self, key: str) -> tuple[bool, Any]:
        with self._lock:
            row = self._db.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
        return (True, json.loads(row[0])) if row else (False, None)

    def _put(self, key: str, value: Any) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO cache (key, value) VALUES (?, ?)", (key, json.dumps(value)))
            self._db.commit()

    async def get_or_compute(self, key: str, fn: Callable[[], Awaitable[Any]]) -> Any:
        found, value = self._get(key)
        if found:
            self.hits += 1
            return value
        self.misses += 1
        if self.mode == "replay":
            raise CacheMiss(
                f"cache miss for {key} in replay mode (SCRIBE_CACHE_MODE=replay). Refresh the cache "
                "locally (run the eval with SCRIBE_CACHE_MODE unset and your provider keys), then "
                "commit/upload the updated cache file."
            )
        async with self._sem:
            value = await fn()
        self._put(key, value)
        return value

    def close(self) -> None:
        self._db.close()
