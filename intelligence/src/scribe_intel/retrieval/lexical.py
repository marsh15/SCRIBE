"""Lexical retrieval: Postgres full-text search, never called "BM25" (Design D10).

* ``chunks.fts_en``: English stemming, heading weight A, body weight B. Query:
  ``websearch_to_tsquery('english', q)``.
* ``chunks.fts_simple``: no stemming/stopwords, for identifiers. Query:
  ``websearch_to_tsquery('simple', q)``.
  Quoted phrases ("annual leave") become phrase (``<->``) queries on both.

One list (INV-10): a chunk is a hit if EITHER column matches. Its score is
``GREATEST(ts_rank_cd(fts_en, qe), ts_rank_cd(fts_simple, qs, weights))``; both values come from the
same ranker (cover density) over the same chunk text, so taking the better of the two is a
comparison of like with like, not a sum of different rankers. ``fts_simple`` has no setweight, so
all its lexemes carry the default D weight (0.1) while the English body is B (0.4): the simple
rank is computed with weights {0.4,0.4,0.4,0.4} so a body-level match is worth the same in both.
A query whose English tsquery is empty (stopword-only, e.g. "the") returns nothing even though the
'simple' config would index the stopword. Normalization flag 1 (divide by 1 + log(length)). Ties break on chunk id, so the order is
deterministic.

Identifier tokenization (measured on pg17, 'simple' config; see docs/scribe2/T13-notes.md):
the parser splits ``ERR_AUTH_403`` into ``err``,``auth``,``403`` and ``INV-2047`` into ``inv`` and
the signed integer ``-2047``; ``NW-COLD-04`` into ``nw-cold``,``nw``,``cold``,``04``. websearch_to_tsquery
applies the same parser and joins the pieces with ``<->``, so an identifier typed exactly as stored
matches as a phrase. What does NOT match is a different separator (``ERR-AUTH-403`` vs a stored
``ERR_AUTH_403`` is fine, but ``INV_2047`` vs a stored ``INV-2047`` is not, because ``-2047`` is one
lexeme). `separator_variant` builds the alternate spelling (hyphen <-> underscore) and the SQL ORs the
two tsqueries. A query of bare ``2047`` still does not hit ``INV-2047``; that is a known gap.
"""

from __future__ import annotations

import re
from typing import Sequence

import psycopg

from .queries import (CANDIDATE_COLUMNS, SCOPE_JOINS, clean_query, in_transaction, row_to_candidate, scope_params,
                      scope_where)
from .types import Candidate

SIMPLE_WEIGHTS = "{0.4,0.4,0.4,0.4}"
RANK_NORMALIZATION = 1
_IDENT = re.compile(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+")


def separator_variant(query: str) -> str | None:
    """Alternate spelling of identifier-like tokens (hyphen <-> underscore), or None if the query
    has none. Applied to the whole query so the AND/phrase structure is preserved."""
    def swap(m: re.Match[str]) -> str:
        tok = m.group(0)
        if "_" in tok and "-" not in tok:
            return tok.replace("_", "-")
        if "-" in tok and "_" not in tok:
            return tok.replace("-", "_")
        return tok

    alt = _IDENT.sub(swap, query)
    return alt if alt != query else None


def lexical_sql(source_filter: bool) -> str:
    qe = "(websearch_to_tsquery('english', %(q)s) || websearch_to_tsquery('english', %(q2)s))"
    qs = "(websearch_to_tsquery('simple', %(q)s) || websearch_to_tsquery('simple', %(q2)s))"
    score = (f"greatest(ts_rank_cd(c.fts_en, {qe}, {RANK_NORMALIZATION}), "
             f"ts_rank_cd('{SIMPLE_WEIGHTS}'::float4[], c.fts_simple, {qs}, {RANK_NORMALIZATION}))")
    return f"""select {CANDIDATE_COLUMNS}, {score} as score
from chunks c
  {SCOPE_JOINS}
  where {scope_where([0] if source_filter else None)}
    and (select numnode({qe})) > 0
    and (c.fts_en @@ {qe} or c.fts_simple @@ {qs})
  order by score desc, c.id
  limit %(k)s"""


async def lexical_search(
    conn: psycopg.AsyncConnection, workspace_id: str, query: str, *, index_profile_id,
    source_ids: Sequence[int] | None = None, k: int = 40,
) -> list[Candidate]:
    q = clean_query(query)
    if not q or k <= 0 or (source_ids is not None and len(source_ids) == 0):
        return []
    # q2 == q when there is no identifier variant (OR of identical tsqueries is harmless).
    params = {**scope_params(workspace_id, index_profile_id, source_ids), "q": q,
              "q2": separator_variant(q) or q, "k": k}
    async with in_transaction(conn):
        cur = await conn.execute(lexical_sql(source_ids is not None), params)
        rows = await cur.fetchall()
    return [row_to_candidate(r, "lexical", i, float(r[10])) for i, r in enumerate(rows, 1)]
