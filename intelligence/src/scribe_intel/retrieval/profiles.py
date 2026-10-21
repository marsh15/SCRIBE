"""Retrieval profiles A-E plus the A5 additions (immutable configs; DESIGN B2 `retrieval_profiles`, ADR-0006).

The plan's section 40.1 list was not available when this was written; these five are the T14
author's choice and the lead may adjust them (a changed setting means a NEW profile name, never
an edit: stored rows are immutable and `upsert_profiles` refuses silent drift).

  A dense-only         dense top-40, no rerank
  B lexical-only       lexical top-40, no rerank
  C hybrid-rrf         dense40 + lexical40 -> RRF(k=60), no rerank
  D dense-rerank       dense40 -> rerank top-20
  E hybrid-rrf-rerank  RRF -> rerank top-20 -> dedup -> parent expansion -> pack 6000 (default)

A5 additions (code only; seeded by `upsert_profiles`, not by a SQL migration). `or_rank` is the OR-based
ranked lexical mode of retrieval/lexical.py (`"and"` ANDs every term and returns nothing for most natural-language
questions):

  B2 lexical-only-v2            B with lexical_mode=or_rank
  C2 hybrid-rrf-v2              C with lexical_mode=or_rank
  E2 hybrid-rrf-rerank-v2       E with lexical_mode=or_rank
  F  hybrid-rrf-rerank-flat     exactly E but expand_parents=False (isolates parent expansion)
  F2 hybrid-rrf-rerank-flat-v2  F with lexical_mode=or_rank

`DEFAULT_PROFILE` is still E: choosing a new default is a later, measured decision.

`config` (what is stored in retrieval_profiles.config) excludes name/description. `lexical_mode` is also
excluded from the stored/canonical JSON while it equals the default `"and"`, so the A-E config JSON and
hashes are byte-identical to the values migration 0012 seeded. `config_hash`
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
SEED_ID_PREFIX = "a0000000-0000-4000-8000-00000000000"  # + 1..5 (profiles A-E only; see SEED_IDS)


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
    # Lexical query mode (retrieval/lexical.py). Omitted from the canonical JSON when "and" (the default) so
    # profiles created before this field existed keep their stored config and hash.
    lexical_mode: Literal["and", "or_rank"] = "and"

    def canonical_dict(self) -> dict[str, Any]:
        d = self.model_dump(mode="json")
        if d["lexical_mode"] == "and":
            del d["lexical_mode"]
        return d


class GateConfig(BaseModel):
    """Answerability gate (DESIGN D13): reranker top score vs `tau`; never cosine similarity.

    UNCALIBRATED PLACEHOLDER. `tau=0.0` rejects nothing; `tau_calibrated=False` says so, and the flag is
    surfaced in the /retrieve response, the chat prompt and the RAG inspector ("gate uncalibrated").
    TODO(T17): set `tau` (and `tau_calibrated=True`) from the dev/validation runs of profiles A-E; do not
    invent a value before then.

    Lives on the profile but OUTSIDE `ProfileConfig`: `retrieval_profiles` rows are immutable and pinned by
    `config_hash` (migration 0012 seeds the hashes), so adding a field to `ProfileConfig` would change every
    stored hash and force new profile rows. Revisit when T17 creates the calibrated profile rows.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tau: float = 0.0
    tau_calibrated: bool = False


class RetrievalProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    config: ProfileConfig
    gate: GateConfig = GateConfig()

    @property
    def config_json(self) -> dict[str, Any]:
        return self.config.canonical_dict()

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

PROFILE_B_V2 = RetrievalProfile(
    name="lexical-only-v2", description="Lexical OR-ranked (or_rank) with identifier boost, top-40, no rerank.",
    config=ProfileConfig(lexical_top_k=40, lexical_mode="or_rank"),
)
PROFILE_C_V2 = RetrievalProfile(
    name="hybrid-rrf-v2",
    description="Dense 40 + lexical or_rank 40 fused by RRF (k=60), no rerank.",
    config=ProfileConfig(dense_top_k=40, lexical_top_k=40, lexical_mode="or_rank"),
)
PROFILE_E_V2 = RetrievalProfile(
    name="hybrid-rrf-rerank-v2",
    description="E with lexical or_rank: RRF(k=60) -> rerank top-20 -> dedup -> parent expansion -> pack 6000 tokens.",
    config=ProfileConfig(
        dense_top_k=40, lexical_top_k=40, rerank=True, rerank_model="rerank-2.5", rerank_top_n=20,
        expand_parents=True, budget_tokens=6000, lexical_mode="or_rank",
    ),
)
PROFILE_F = RetrievalProfile(
    name="hybrid-rrf-rerank-flat",
    description="E without parent expansion (child chunks packed as retrieved): isolates the parent-expansion effect.",
    config=ProfileConfig(
        dense_top_k=40, lexical_top_k=40, rerank=True, rerank_model="rerank-2.5", rerank_top_n=20,
        expand_parents=False, budget_tokens=6000,
    ),
)
PROFILE_F_V2 = RetrievalProfile(
    name="hybrid-rrf-rerank-flat-v2",
    description="F with lexical or_rank.",
    config=ProfileConfig(
        dense_top_k=40, lexical_top_k=40, rerank=True, rerank_model="rerank-2.5", rerank_top_n=20,
        expand_parents=False, budget_tokens=6000, lexical_mode="or_rank",
    ),
)

# A-E: the profiles migration 0012 seeds (ids SEED_ID_PREFIX + "1".."5"). Unchanged.
SEED_PROFILES: tuple[RetrievalProfile, ...] = (PROFILE_A, PROFILE_B, PROFILE_C, PROFILE_D, PROFILE_E)
# A5 additions; seeded through `upsert_profiles`/`seed_sql` (no SQL migration).
V2_PROFILES: tuple[RetrievalProfile, ...] = (PROFILE_B_V2, PROFILE_C_V2, PROFILE_E_V2, PROFILE_F, PROFILE_F_V2)
ALL_PROFILES: tuple[RetrievalProfile, ...] = SEED_PROFILES + V2_PROFILES
DEFAULT_PROFILE = PROFILE_E  # a new default is a later, measured decision
BY_NAME = {p.name: p for p in ALL_PROFILES}

# Stable explicit row id per profile (SEED_ID_PREFIX + digit only works for one digit).
SEED_IDS: dict[str, str] = {
    **{p.name: f"{SEED_ID_PREFIX}{i}" for i, p in enumerate(SEED_PROFILES, start=1)},
    "lexical-only-v2": "a0000000-0000-4000-8000-000000000006",
    "hybrid-rrf-v2": "a0000000-0000-4000-8000-000000000007",
    "hybrid-rrf-rerank-v2": "a0000000-0000-4000-8000-000000000008",
    "hybrid-rrf-rerank-flat": "a0000000-0000-4000-8000-000000000009",
    "hybrid-rrf-rerank-flat-v2": "a0000000-0000-4000-8000-000000000010",
}


def gate_for(profile_name: str) -> GateConfig:
    """The answerability gate of a profile; strategies that are not seeded profiles get the uncalibrated default."""
    p = BY_NAME.get(profile_name)
    return p.gate if p is not None else GateConfig()

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
        (UPSERT_SQL, (SEED_IDS[p.name], p.name, canonical_json(p.config_json), p.config_hash))
        for p in ALL_PROFILES
    ]


def upsert_profiles(conn: Any) -> None:
    """Insert missing profiles; verify existing rows match byte-for-byte (immutability).

    `conn` is a sync psycopg connection (anything with `.execute(sql, params)` returning a
    cursor with `.fetchone()`). Raises ProfileImmutableError if a stored row differs."""
    for (sql, params), p in zip(seed_sql(), ALL_PROFILES):
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
