"""Lexical retrieval: Postgres full-text search, never called "BM25" (Design D10).

Two modes, selected by the profile's ``lexical_mode`` and threaded through ``lexical_search``:

``"and"`` (default; the mode of profiles A-E, SQL unchanged)
  * ``chunks.fts_en``: English stemming, heading weight A, body weight B. Query:
    ``websearch_to_tsquery('english', q)``.
  * ``chunks.fts_simple``: no stemming/stopwords, for identifiers. Query:
    ``websearch_to_tsquery('simple', q)``.
    Quoted phrases ("annual leave") become phrase (``<->``) queries on both.
  ``websearch_to_tsquery`` ANDs every term, so a natural-language question ("What is the notice period
  if I want to leave?") usually matches no chunk at all: measured on golden-v1, "and" mode returns an
  empty list for most questions, and a hybrid profile built on it degenerates to dense-only.

``"or_rank"`` (profiles ``*-v2`` and ``hybrid-rrf-rerank-flat*``)
  * English side: the lexemes of ``plainto_tsquery('english', q)`` (stopwords stripped, stemmed), joined
    with OR (``|``) instead of AND (``&``). The query is built in SQL from Postgres' own tsquery text
    (``replace(plainto_tsquery(...)::text, ' & ', ' | ')::tsquery``); that text is made of quoted lexemes
    only, so no user text is ever spliced into SQL (query text is only a bound parameter). Chunks are
    ranked by ``ts_rank_cd(fts_en, q_or, OR_RANK_NORMALIZATION)``: more distinct query terms in a
    tighter cover rank higher; a chunk matching one common term ranks low but is still a candidate.
  * Identifier boost: tokens shaped like identifiers (``FUEL-SUR``, ``ERR_AUTH_403``, ``INV-2047``,
    ``ISO27001``; see ``IDENT_PATTERNS``) are matched on ``fts_simple`` exactly as today (phrase
    semantics of ``websearch_to_tsquery('simple', token)``, ORed with the ``separator_variant`` spelling).
    A chunk that matches any identifier gets the fixed additive ``IDENT_BOOST`` on top of its English
    rank, and is a hit even if the English OR query misses it. The boost is larger than any
    ``ts_rank_cd`` value with normalization 1, so identifier chunks lead, ordered by their own rank.
  * A chunk is a hit if it matches the OR query or an identifier. A stopword-only query with no
    identifier returns nothing. Ties break on chunk id.
  ``IDENT_BOOST`` and ``OR_RANK_NORMALIZATION`` are module constants: changing them changes retrieval
  behaviour, so a later tuning pass must create NEW profile names (profiles are immutable) that ship with
  the changed constants; they are not part of ``ProfileConfig``.

"and" mode scoring and tokenization notes (unchanged):

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
from typing import Literal, Sequence

import psycopg

from .queries import (CANDIDATE_COLUMNS, SCOPE_JOINS, clean_query, in_transaction, row_to_candidate, scope_params,
                      scope_where)
from .types import Candidate

SIMPLE_WEIGHTS = "{0.4,0.4,0.4,0.4}"
RANK_NORMALIZATION = 1
_IDENT = re.compile(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+")

LexicalMode = Literal["and", "or_rank"]
DEFAULT_LEXICAL_MODE: LexicalMode = "and"

# or_rank knobs. Changing any of these changes retrieval behaviour: ship it under a NEW profile name.
IDENT_BOOST = 1.0              # fixed additive score for a chunk matching an identifier token
OR_RANK_NORMALIZATION = 1      # ts_rank_cd normalization flag for the English OR query (1 = / (1 + log(length)))
MAX_IDENT_TOKENS = 8           # identifiers considered per query (each also tries its separator variant)

# Identifier shapes (searched in the raw query, case preserved where the shape needs it).
IDENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # ERR_AUTH_403, FUEL-SUR, NW-COLD-04, INV-2047
    re.compile(r"(?<![A-Za-z0-9_-])[A-Z]{2,}(?:[-_][A-Z0-9]+)+(?![A-Za-z0-9])"),
    # lower/mixed-case code with a digit part: inv-2047, err-auth-403, Err_403_x
    re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z]{2,}(?:[-_][A-Za-z]+)*[-_]\d[A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*(?![A-Za-z0-9])"),
    # letters+digits without separator, at least 4 chars: ISO27001, SOC2024, A4B7
    re.compile(r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{4,}(?![A-Za-z0-9])"),
)


def identifier_tokens(query: str) -> list[str]:
    """Identifier-shaped tokens of `query` (first occurrence order, de-duplicated, capped), each followed by its
    hyphen/underscore variant when it has one. Pure function of the text; the result is only ever bound as parameters."""
    found: list[str] = []
    for pat in IDENT_PATTERNS:
        for m in pat.finditer(query):
            tok = m.group(0)
            if tok.lower() not in {t.lower() for t in found}:
                found.append(tok)
    found = found[:MAX_IDENT_TOKENS]
    out: list[str] = []
    for tok in found:
        out.append(tok)
        alt = separator_variant(tok)
        if alt is not None:
            out.append(alt)
    return out


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


def lexical_sql(source_filter: bool, mode: LexicalMode = "and") -> str:
    if mode == "or_rank":
        return _or_rank_sql(source_filter)
    if mode != "and":
        raise ValueError(f"unknown lexical_mode {mode!r}")
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


def _or_rank_sql(source_filter: bool) -> str:
    """or_rank statement. Parameters: %(q)s the cleaned query, %(idents)s text[] of identifier tokens (may be
    empty), %(k)s. The tsqueries are built from Postgres-generated tsquery text (quoted lexemes), never from
    query text spliced into SQL. Numeric knobs are module constants, formatted as literals."""
    return f"""with q as (
  select replace(plainto_tsquery('english', %(q)s)::text, ' & ', ' | ')::tsquery as qor,
         (select nullif(string_agg('(' || t.tq::text || ')', ' | '), '')::tsquery
            from (select websearch_to_tsquery('simple', v) as tq from unnest(%(idents)s::text[]) as v) t
           where numnode(t.tq) > 0) as qid
)
select {CANDIDATE_COLUMNS},
       ts_rank_cd(c.fts_en, q.qor, {int(OR_RANK_NORMALIZATION)})
         + case when q.qid is not null and c.fts_simple @@ q.qid then {float(IDENT_BOOST)!r} else 0 end as score
from chunks c
  {SCOPE_JOINS}
  cross join q
  where {scope_where([0] if source_filter else None)}
    and (c.fts_en @@ q.qor or (q.qid is not null and c.fts_simple @@ q.qid))
  order by score desc, c.id
  limit %(k)s"""


async def lexical_search(
    conn: psycopg.AsyncConnection, workspace_id: str, query: str, *, index_profile_id,
    source_ids: Sequence[int] | None = None, k: int = 40, lexical_mode: LexicalMode = DEFAULT_LEXICAL_MODE,
) -> list[Candidate]:
    q = clean_query(query)
    if not q or k <= 0 or (source_ids is not None and len(source_ids) == 0):
        return []
    if lexical_mode == "or_rank":
        params = {**scope_params(workspace_id, index_profile_id, source_ids), "q": q,
                  "idents": identifier_tokens(q), "k": k}
    elif lexical_mode == "and":
        # q2 == q when there is no identifier variant (OR of identical tsqueries is harmless).
        params = {**scope_params(workspace_id, index_profile_id, source_ids), "q": q,
                  "q2": separator_variant(q) or q, "k": k}
    else:
        raise ValueError(f"unknown lexical_mode {lexical_mode!r}")
    async with in_transaction(conn):
        cur = await conn.execute(lexical_sql(source_ids is not None, lexical_mode), params)
        rows = await cur.fetchall()
    return [row_to_candidate(r, "lexical", i, float(r[10])) for i, r in enumerate(rows, 1)]
