"""Adapter: scribe_eval.cache.DiskCache -> the get/put interface scribe_ingest.embedding expects.

scribe_ingest never imports scribe_eval for this; the caller passes this adapter in.
Keys are the embedder's own D18 keys (model, dims, task, sha256(text)).
"""

from __future__ import annotations

from .cache import CacheMiss, DiskCache


class DiskEmbeddingCache:
    def __init__(self, cache: DiskCache) -> None:
        self.cache = cache

    def get(self, key: str) -> list[float] | None:
        found, value = self.cache._get(key)  # noqa: SLF001 - same package
        if found:
            self.cache.hits += 1
            return value
        self.cache.misses += 1
        if self.cache.mode == "replay":
            raise CacheMiss(f"embedding cache miss in replay mode ({key[:60]}...)")
        return None

    def put(self, key: str, value: list[float]) -> None:
        self.cache._put(key, value)  # noqa: SLF001
