"""SQL building blocks shared by the dense and lexical retrievers (T13).

INV-2/INV-3: every filter (workspace on every table, generation ACTIVE for the profile's index
profile, pointer in active_indexes, source not deleted, optional source ids) is part of the
WHERE/JOIN clauses of the same statement that carries ORDER BY ... LIMIT. Nothing is filtered
after the LIMIT. Query text is only ever passed as a bound parameter.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from typing import Any, Sequence

import psycopg

from .types import Candidate, Method

MAX_QUERY_CHARS = 2000

# FROM-clause tail that follows `chunks c`. Parameters: %(w)s workspace uuid, %(p)s index profile uuid.
SCOPE_JOINS = """
  join index_generations g on g.id = c.generation_id and g.workspace_id = %(w)s
       and g.status = 'ACTIVE' and g.index_profile_id = %(p)s and g.source_version_id = c.source_version_id
  join active_indexes ai on ai.generation_id = g.id and ai.source_id = g.source_id
       and ai.index_profile_id = g.index_profile_id and ai.workspace_id = %(w)s
  join files f on f.id = g.source_id and f.workspace_id = %(w)s and f.deleted_at is null
"""


def scope_where(source_ids: Sequence[int] | None, *, children_only: bool = True) -> str:
    parts = ["c.workspace_id = %(w)s"]
    if children_only:
        parts.append("c.parent_id is not null")
    if source_ids is not None:
        parts.append("g.source_id = any(%(src)s)")
    return " and ".join(parts)


CANDIDATE_COLUMNS = (
    "c.id, c.parent_id, g.source_id, c.source_version_id, c.generation_id, f.name, c.content, "
    "c.heading_text, c.content_hash, c.token_count"
)


def scope_params(workspace_id: str, index_profile_id: uuid.UUID | str,
                 source_ids: Sequence[int] | None) -> dict[str, Any]:
    params: dict[str, Any] = {"w": str(workspace_id), "p": str(index_profile_id)}
    if source_ids is not None:
        params["src"] = [int(s) for s in source_ids]
    return params


def vec_literal(v: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


def row_to_candidate(row: Sequence[Any], method: Method, rank: int, score: float) -> Candidate:
    (cid, parent_id, source_id, version_id, gen_id, name, content, heading, chash, tokens) = row[:10]
    return Candidate(chunk_id=cid, parent_id=parent_id, source_id=source_id, source_version_id=version_id,
                     generation_id=gen_id, doc_name=name, content=content, heading_text=heading,
                     content_hash=chash, token_count=tokens, method=method, rank=rank, score=float(score))


def clean_query(text: str) -> str:
    """Strip NULs (Postgres rejects them), collapse whitespace, cap the length."""
    return re.sub(r"\s+", " ", text.replace("\x00", " ")).strip()[:MAX_QUERY_CHARS]


def in_transaction(conn: psycopg.AsyncConnection):
    """Async context manager: join the caller's transaction, or open one if the (autocommit)
    connection is idle, so transaction-local settings (set_config(..., true)) never leak."""
    if conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE:
        return conn.transaction()
    return contextlib.nullcontext()
