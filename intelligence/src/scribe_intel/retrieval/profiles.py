"""Retrieval profiles A-E (immutable configs; DESIGN B2 `retrieval_profiles`, ADR-0006).

The plan's section 40.1 list was not available when this was written; these five are the T14
author's choice and the lead may adjust them (a changed setting means a NEW profile name, never
an edit: stored rows are immutable and `upsert_profiles` refuses silent drift).

  A dense-only         dense top-40, no rerank
  B lexical-only       lexical top-40, no rerank
  C hybrid-rrf         dense40 + lexical40 -> RRF(k=60), no rerank
  D dense-rerank       dense40 -> rerank top-20
  E hybrid-rrf-rerank  RRF -> rerank top-20 -> dedup -> parent expansion -> pack 6000 (default)

`config` (what is stored in retrieval_profiles.config) excludes name/description. `config_hash`
is sha256 of the canonical JSON (sorted keys, compact separators, UTF-8). NOTE: this is a Python
canonical-JSON hash, not the `sha256(jsonb::text)` the SQL seeds in 0011 use for other profile
tables; it is stable across processes and verified on every upsert.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = 1
SEED_ID_PREFIX = "a0000000-0000-4000-8000-00000000000"  # + 1..5


class ProfileConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    dense_top_k: int | None = None  # None = dense retriever not used
    lexical_top_k: int | None = None
    fusion: Literal["rrf"] = "rrf"  # single-list profiles pass through RRF (order preserved)
    rrf_k: int = 60
    rerank: bool = False
    rerank_model: str | None = None
    rerank_top_n: int = 20
    dedup: bool = True
    expand_parents: bool = False
    budget_tokens: int = 6000


class RetrievalProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    config: ProfileConfig

    @property
    def config_json(self) -> dict[str, Any]:
        return self.config.model_dump(mode="json")

    @property
    def config_hash(self) -> str:
        return hash_config(self.config_json)


def canonical_json(config: dict[str, Any]) -> str:
    return json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_config(config: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()


PROFILE_A = RetrievalProfile(
    name="dense-only", description="Dense top-40, no rerank.",
    config=ProfileConfig(dense_top_k=40),
)
PROFILE_B = RetrievalProfile(
    name="lexical-only", description="Lexical (FTS) top-40, no rerank.",
    config=ProfileConfig(lexical_top_k=40),
)
PROFILE_C = RetrievalProfile(
    name="hybrid-rrf", description="Dense 40 + lexical 40 fused by RRF (k=60), no rerank.",
    config=ProfileConfig(dense_top_k=40, lexical_top_k=40),
)
PROFILE_D = RetrievalProfile(
    name="dense-rerank", description="Dense top-40, rerank top-20.",
    config=ProfileConfig(dense_top_k=40, rerank=True, rerank_model="rerank-2.5", rerank_top_n=20),
)
PROFILE_E = RetrievalProfile(
    name="hybrid-rrf-rerank",
    description="Default: RRF(k=60) -> rerank top-20 -> dedup -> parent expansion -> pack 6000 tokens.",
    config=ProfileConfig(
        dense_top_k=40, lexical_top_k=40, rerank=True, rerank_model="rerank-2.5", rerank_top_n=20,
        expand_parents=True, budget_tokens=6000,
    ),
)

SEED_PROFILES: tuple[RetrievalProfile, ...] = (PROFILE_A, PROFILE_B, PROFILE_C, PROFILE_D, PROFILE_E)
DEFAULT_PROFILE = PROFILE_E
BY_NAME = {p.name: p for p in SEED_PROFILES}

UPSERT_SQL = (
    'INSERT INTO retrieval_profiles (id, name, config, config_hash) VALUES (%s::uuid, %s, %s::jsonb, %s) '
    "ON CONFLICT (name) DO NOTHING"
)
SELECT_SQL = "SELECT config, config_hash FROM retrieval_profiles WHERE name = %s"


class ProfileImmutableError(RuntimeError):
    pass


def seed_sql() -> list[tuple[str, tuple[str, str, str, str]]]:
    """(sql, params) pairs, one per profile, for any DB-API cursor (psycopg `%s` style)."""
    return [
        (UPSERT_SQL, (f"{SEED_ID_PREFIX}{i}", p.name, canonical_json(p.config_json), p.config_hash))
        for i, p in enumerate(SEED_PROFILES, start=1)
    ]


def upsert_profiles(conn: Any) -> None:
    """Insert missing profiles; verify existing rows match byte-for-byte (immutability).

    `conn` is a sync psycopg connection (anything with `.execute(sql, params)` returning a
    cursor with `.fetchone()`). Raises ProfileImmutableError if a stored row differs."""
    for (sql, params), p in zip(seed_sql(), SEED_PROFILES):
        conn.execute(sql, params)
        row = conn.execute(SELECT_SQL, (p.name,)).fetchone()
        if row is None:
            raise ProfileImmutableError(f"profile {p.name!r} missing after upsert")
        stored_config, stored_hash = (row["config"], row["config_hash"]) if isinstance(row, dict) else row
        if isinstance(stored_config, str):
            stored_config = json.loads(stored_config)
        if stored_hash != p.config_hash or hash_config(stored_config) != p.config_hash:
            raise ProfileImmutableError(
                f"stored retrieval profile {p.name!r} differs from code (stored hash {stored_hash}, "
                f"code hash {p.config_hash}). Profiles are immutable: add a new profile name instead."
            )
