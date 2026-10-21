"""A5: lexical `or_rank` mode, identifier boost, and the new immutable profiles.

`and` mode is pinned byte-for-byte (SQL string captured before the A5 change) and so are the A-E config hashes.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest
from conftest import DATABASE_URL_TEST, needs_db
from _t13 import conn_for, corpus_workspace, seed_sync
from scribe_intel.retrieval import lexical as L
from scribe_intel.retrieval import profiles as P
from scribe_intel.retrieval.lexical import identifier_tokens, lexical_search, lexical_sql

REPO = Path(__file__).resolve().parents[3]
GOLDEN = REPO / "benchmarks" / "datasets" / "golden-v1" / "cases.jsonl"
MIGRATION = REPO / "drizzle" / "0012_seed_retrieval_profiles.sql"

AND_SQL_SOURCE_FILTER = "select c.id, c.parent_id, g.source_id, c.source_version_id, c.generation_id, f.name, c.content, c.heading_text, c.content_hash, c.token_count, greatest(ts_rank_cd(c.fts_en, (websearch_to_tsquery('english', %(q)s) || websearch_to_tsquery('english', %(q2)s)), 1), ts_rank_cd('{0.4,0.4,0.4,0.4}'::float4[], c.fts_simple, (websearch_to_tsquery('simple', %(q)s) || websearch_to_tsquery('simple', %(q2)s)), 1)) as score\nfrom chunks c\n  \n  join index_generations g on g.id = c.generation_id and g.workspace_id = %(w)s\n       and g.status = 'ACTIVE' and g.index_profile_id = %(p)s and g.source_version_id = c.source_version_id\n  join active_indexes ai on ai.generation_id = g.id and ai.source_id = g.source_id\n       and ai.index_profile_id = g.index_profile_id and ai.workspace_id = %(w)s\n  join files f on f.id = g.source_id and f.workspace_id = %(w)s and f.deleted_at is null\n\n  where c.workspace_id = %(w)s and c.parent_id is not null and g.source_id = any(%(src)s)\n    and (select numnode((websearch_to_tsquery('english', %(q)s) || websearch_to_tsquery('english', %(q2)s)))) > 0\n    and (c.fts_en @@ (websearch_to_tsquery('english', %(q)s) || websearch_to_tsquery('english', %(q2)s)) or c.fts_simple @@ (websearch_to_tsquery('simple', %(q)s) || websearch_to_tsquery('simple', %(q2)s)))\n  order by score desc, c.id\n  limit %(k)s"
AND_SQL_NO_FILTER = "select c.id, c.parent_id, g.source_id, c.source_version_id, c.generation_id, f.name, c.content, c.heading_text, c.content_hash, c.token_count, greatest(ts_rank_cd(c.fts_en, (websearch_to_tsquery('english', %(q)s) || websearch_to_tsquery('english', %(q2)s)), 1), ts_rank_cd('{0.4,0.4,0.4,0.4}'::float4[], c.fts_simple, (websearch_to_tsquery('simple', %(q)s) || websearch_to_tsquery('simple', %(q2)s)), 1)) as score\nfrom chunks c\n  \n  join index_generations g on g.id = c.generation_id and g.workspace_id = %(w)s\n       and g.status = 'ACTIVE' and g.index_profile_id = %(p)s and g.source_version_id = c.source_version_id\n  join active_indexes ai on ai.generation_id = g.id and ai.source_id = g.source_id\n       and ai.index_profile_id = g.index_profile_id and ai.workspace_id = %(w)s\n  join files f on f.id = g.source_id and f.workspace_id = %(w)s and f.deleted_at is null\n\n  where c.workspace_id = %(w)s and c.parent_id is not null\n    and (select numnode((websearch_to_tsquery('english', %(q)s) || websearch_to_tsquery('english', %(q2)s)))) > 0\n    and (c.fts_en @@ (websearch_to_tsquery('english', %(q)s) || websearch_to_tsquery('english', %(q2)s)) or c.fts_simple @@ (websearch_to_tsquery('simple', %(q)s) || websearch_to_tsquery('simple', %(q2)s)))\n  order by score desc, c.id\n  limit %(k)s"

# captured from the code before the A5 change; equal to the hashes migration 0012 seeds
PINNED_HASHES = {
    "dense-only": "96bb43a1b30381e8d71d8c7f577663da44f9e8a7f6f47bdcdf8fa0393f70170b",
    "lexical-only": "4800e93dd887e5d8a37be200dd29183c412231b876ca340618f5e9a47ebb9ece",
    "hybrid-rrf": "645b00f134edb5e06523f1f767e5dd82818870323f2f9d1364b6e61367eeecca",
    "dense-rerank": "ba3010453440ce650047a11a5309f229c9be68602649254b097cf572b33de0ea",
    "hybrid-rrf-rerank": "0117d32b812ec48088c8e0026523a05e7a08fb713853e3eace4f554d67050d12",
}


# ---- DB-free: SQL, constants, identifier extraction

def test_and_mode_sql_is_unchanged():
    assert lexical_sql(True) == AND_SQL_SOURCE_FILTER
    assert lexical_sql(False) == AND_SQL_NO_FILTER
    assert lexical_sql(True, "and") == AND_SQL_SOURCE_FILTER
    assert L.DEFAULT_LEXICAL_MODE == "and"


def test_or_rank_sql_shape_and_named_constants():
    sql = lexical_sql(True, "or_rank")
    assert "plainto_tsquery('english', %(q)s)" in sql and "' | '" in sql
    assert "ts_rank_cd(c.fts_en, q.qor, 1)" in sql and "order by score desc, c.id" in sql
    assert "websearch_to_tsquery('simple', v)" in sql and "%(idents)s::text[]" in sql
    assert sql.index("g.status = 'ACTIVE'") < sql.index("limit") and "deleted_at is null" in sql
    assert L.IDENT_BOOST == 1.0 and L.OR_RANK_NORMALIZATION == 1
    assert lexical_sql(False, "or_rank") != sql
    with pytest.raises(ValueError):
        lexical_sql(True, "nope")  # type: ignore[arg-type]


@pytest.mark.parametrize("q,expected", [
    ("What is FUEL-SUR on the price list?", ["FUEL-SUR", "FUEL_SUR"]),
    ("ERR_AUTH_403 and INV-2047", ["ERR_AUTH_403", "ERR-AUTH-403", "INV-2047", "INV_2047"]),
    ("sku NW-COLD-04", ["NW-COLD-04", "NW_COLD_04"]),
    ("inv-2047", ["inv-2047", "inv_2047"]),
    ("Explain err-auth-403 please", ["err-auth-403", "err_auth_403"]),
    ("ISO27001 audit", ["ISO27001"]),
    ("plain natural language question", []),
    ("Helios SLA, 36-month term", []),
    ("", []),
])
def test_identifier_tokens(q, expected):
    assert identifier_tokens(q) == expected


def test_identifier_tokens_are_capped_and_deduplicated():
    toks = identifier_tokens(" ".join(f"AB-{i}X" for i in range(50)) + " AB-1X")
    assert len(toks) <= 2 * L.MAX_IDENT_TOKENS
    assert len(set(toks)) == len(toks)


# ---- DB-free: profiles

def _migration_rows() -> dict[str, tuple[str, str, str]]:
    rx = re.compile(r"\('([0-9a-f-]{36})',\s*'([^']+)',\s*'(\{[^']*\})',\s*'([0-9a-f]{64})'\)")
    return {name: (rid, cfg, h) for rid, name, cfg, h in rx.findall(MIGRATION.read_text(encoding="utf-8"))}


def test_a_to_e_hashes_and_config_json_unchanged():
    mig = _migration_rows()
    assert set(mig) == set(PINNED_HASHES) == {p.name for p in P.SEED_PROFILES}
    for p in P.SEED_PROFILES:
        assert p.config_hash == PINNED_HASHES[p.name] == mig[p.name][2], p.name
        assert "lexical_mode" not in p.config_json and p.config.lexical_mode == "and"
        assert P.canonical_json(p.config_json) == mig[p.name][1], p.name  # byte-identical stored JSON
        assert mig[p.name][0] == P.SEED_IDS[p.name] == f"{P.SEED_ID_PREFIX}{list(PINNED_HASHES).index(p.name) + 1}"


def test_explicit_default_lexical_mode_is_still_omitted():
    cfg = P.ProfileConfig(dense_top_k=40, lexical_mode="and")
    assert "lexical_mode" not in cfg.canonical_dict()
    assert P.RetrievalProfile(name="x", description="", config=cfg).config_hash == P.PROFILE_A.config_hash


def test_new_profiles_definitions():
    by = P.BY_NAME
    assert [p.name for p in P.V2_PROFILES] == ["lexical-only-v2", "hybrid-rrf-v2", "hybrid-rrf-rerank-v2",
                                               "hybrid-rrf-rerank-flat", "hybrid-rrf-rerank-flat-v2"]
    assert [p.name for p in P.ALL_PROFILES] == [p.name for p in P.SEED_PROFILES + P.V2_PROFILES]
    for name in ("lexical-only-v2", "hybrid-rrf-v2", "hybrid-rrf-rerank-v2", "hybrid-rrf-rerank-flat-v2"):
        assert by[name].config_json["lexical_mode"] == "or_rank"
    assert "lexical_mode" not in by["hybrid-rrf-rerank-flat"].config_json
    e = P.PROFILE_E.config
    assert by["lexical-only-v2"].config == P.PROFILE_B.config.model_copy(update={"lexical_mode": "or_rank"})
    assert by["hybrid-rrf-v2"].config == P.PROFILE_C.config.model_copy(update={"lexical_mode": "or_rank"})
    assert by["hybrid-rrf-rerank-v2"].config == e.model_copy(update={"lexical_mode": "or_rank"})
    assert by["hybrid-rrf-rerank-flat"].config == e.model_copy(update={"expand_parents": False})
    assert by["hybrid-rrf-rerank-flat-v2"].config == e.model_copy(update={"expand_parents": False, "lexical_mode": "or_rank"})
    assert P.DEFAULT_PROFILE is P.PROFILE_E and P.DEFAULT_PROFILE.name == "hybrid-rrf-rerank"
    assert len({p.config_hash for p in P.ALL_PROFILES}) == 10 and len({p.name for p in P.ALL_PROFILES}) == 10


def test_seed_ids_are_valid_distinct_uuids():
    assert set(P.SEED_IDS) == {p.name for p in P.ALL_PROFILES}
    parsed = [uuid.UUID(v) for v in P.SEED_IDS.values()]
    assert len(set(parsed)) == 10 and all(str(u) == v for u, v in zip(parsed, P.SEED_IDS.values()))
    assert all(u.version == 4 for u in parsed)
    assert [params[0] for _, params in P.seed_sql()] == [P.SEED_IDS[p.name] for p in P.ALL_PROFILES]


def test_every_profile_has_a_registered_strategy():
    from scribe_intel.retrieval.registry import get_handler
    import scribe_intel.retrieval.strategies.hybrid  # noqa: F401  (registers the profile handlers)
    for p in P.ALL_PROFILES:
        assert get_handler(p.name) is not None, p.name


# ---- DB-backed

pytestmark_db = needs_db


@pytest.fixture(scope="module")
def seeded():
    return seed_sync(DATABASE_URL_TEST)


@pytest.fixture(scope="module")
def corpus():
    return corpus_workspace(DATABASE_URL_TEST)


@pytest.fixture()
async def conn():
    c = await conn_for(DATABASE_URL_TEST)
    yield c
    await c.close()


async def orr(conn, ws, q, **kw):
    return await lexical_search(conn, ws["wa"] if "wa" in ws else ws["wid"], q, index_profile_id=ws["profile_id"],
                                lexical_mode="or_rank", **kw)


async def andm(conn, ws, q, **kw):
    return await lexical_search(conn, ws["wa"] if "wa" in ws else ws["wid"], q, index_profile_id=ws["profile_id"], **kw)


def _first_20_dev_questions() -> list[dict]:
    cases = [json.loads(l) for l in GOLDEN.read_text(encoding="utf-8").splitlines() if l.strip()]
    dev = sorted((c for c in cases if c["split"] == "dev" and c["answerable"]), key=lambda c: c["id"])
    assert len(dev) >= 20
    return dev[:20]


@needs_db
async def test_or_rank_returns_results_for_natural_language_questions(conn, corpus, capsys):
    qs = _first_20_dev_questions()
    or_hits = and_hits = 0
    for c in qs:
        if await orr(conn, corpus, c["question"]):
            or_hits += 1
        if await andm(conn, corpus, c["question"]):
            and_hits += 1
    with capsys.disabled():
        print(f"\nA5 counts over {len(qs)} dev questions: or_rank non-empty={or_hits}, and non-empty={and_hits}")
    assert or_hits >= 18
    assert and_hits <= 10  # "and" is empty for most of them
    assert or_hits > and_hits


@needs_db
async def test_identifier_boost_ranks_price_list_first_for_id_16(conn, corpus, capsys):
    case = next(json.loads(l) for l in GOLDEN.read_text(encoding="utf-8").splitlines() if '"id-16"' in l)
    assert "FUEL-SUR" in case["question"]
    res = await orr(conn, corpus, case["question"])
    with capsys.disabled():
        print(f"\nA5 id-16 or_rank top3: {[(c.doc_name, round(c.score, 3)) for c in res[:3]]}")
    assert res and res[0].doc_name == "price-list-2026.csv" and "FUEL-SUR" in res[0].content
    assert res[0].score >= L.IDENT_BOOST
    # the boost is what does it: without it the identifier chunk is not guaranteed to lead
    assert all(c.score < L.IDENT_BOOST for c in res if "FUEL-SUR" not in c.content)


@needs_db
async def test_identifier_boost_in_toy_workspace_and_variant_spelling(conn, seeded):
    for q in ("Which ticket fixes ERR_AUTH_403 for revoked keys?", "what is INV_2047", "Explain err-auth-403 please"):
        res = await orr(conn, seeded, q)
        assert res and res[0].doc_name == "api-errors.md", (q, [(c.doc_name, c.score) for c in res])
        assert res[0].score >= L.IDENT_BOOST


@needs_db
async def test_hit_if_or_query_or_identifier_matches(conn, seeded):
    # only the identifier matches (every other term is unknown to the corpus)
    res = await orr(conn, seeded, "zzqqxx INV-2047 wwkkjj")
    assert [c.doc_name for c in res] == ["api-errors.md"]
    # no identifier, one of several terms matches
    res = await orr(conn, seeded, "zzqqxx budget wwkkjj")
    assert res and all("budget" in c.content.lower() for c in res)
    assert await orr(conn, seeded, "zzqqxx wwkkjj") == []


@needs_db
async def test_or_rank_ranks_more_matching_terms_higher(conn, seeded):
    res = await orr(conn, seeded, "annual leave days")
    names = [c.doc_name for c in res]
    assert names[0] == "hr.md" and {"hr.md", "scatter.md", "stem.md"} <= set(names)
    assert [c.score for c in res] == sorted((c.score for c in res), reverse=True)
    assert [c.rank for c in res] == list(range(1, len(res) + 1)) and all(c.method == "lexical" for c in res)


@needs_db
@pytest.mark.parametrize("q", ["", "   ", "the", "the of and a", "what is the", "\x00"])
async def test_or_rank_empty_and_stopword_only_return_nothing(conn, seeded, q):
    assert await orr(conn, seeded, q) == []


@needs_db
async def test_or_rank_is_deterministic_and_ties_break_on_chunk_id(conn, seeded):
    a = await orr(conn, seeded, "error codes tickets routine notes", k=40)
    b = await orr(conn, seeded, "error codes tickets routine notes", k=40)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b] and len(a) >= 3
    ties = [(x, y) for x, y in zip(a, a[1:]) if x.score == y.score]
    assert ties and all(str(x.chunk_id) < str(y.chunk_id) for x, y in ties)
    assert len(await orr(conn, seeded, "error codes tickets", k=2)) == 2


@needs_db
async def test_or_rank_source_ids_filter(conn, seeded):
    res = await orr(conn, seeded, "leave", source_ids=[seeded["a_ids"]["stem.md"]])
    assert [c.doc_name for c in res] == ["stem.md"]
    assert await orr(conn, seeded, "leave", source_ids=[]) == []


@needs_db
async def test_or_rank_respects_workspace_scope(conn, seeded):
    # b-only.md lives only in workspace B and matches "platypus sanctuary"; workspace A must not see it
    assert await orr(conn, {**seeded, "wa": seeded["wa"]}, "platypus sanctuary") == []
    res = await orr(conn, {**seeded, "wa": seeded["wb"]}, "platypus sanctuary")
    assert res and res[0].doc_name == "b-only.md"


HOSTILE = [
    "'; drop table chunks; --", '" unbalanced quote', "a & b | !c <-> d", "\\", "%s %(q)s {} %%", "x\x00y",
    "(((", "-", "- -foo", "or or or", "ünïcödé 日本語 émoji 😀", "'" * 50, "a" * 5000, "<script>alert(1)</script>",
    "&|!():*", "'a' & 'b' | ('c' <-> 'd'):*", "ERR_AUTH_403'; drop table chunks; --", "AB-1X " * 100,
    "FUEL-SUR \\ ' \" & | ! ( ) : *", "INV-2047:*", "a'b-1c'd", "NEAR(a b) FOLLOWED BY",
]


@needs_db
@pytest.mark.parametrize("q", HOSTILE)
async def test_or_rank_hostile_input_is_safe(conn, seeded, q):
    res = await orr(conn, seeded, q)
    assert isinstance(res, list)
    cur = await conn.execute("select count(*) from chunks where workspace_id=%s", (seeded["wa"],))
    assert (await cur.fetchone())[0] > 0  # nothing was dropped
    # query text is only ever a bound parameter: nothing user-supplied appears in the statement
    sql = lexical_sql(True, "or_rank")
    assert q.strip()[:20] not in sql or q.strip()[:20] == ""


@needs_db
async def test_or_rank_never_interpolates_query_text(conn, seeded, monkeypatch):
    seen = []
    real = type(conn).execute

    async def spy(self, query, params=None, **kw):
        seen.append((str(query), params))
        return await real(self, query, params, **kw)

    monkeypatch.setattr(type(conn), "execute", spy)
    evil = "x'); drop table chunks; -- FUEL-SUR"
    await orr(conn, seeded, evil)
    stmts = [(s, p) for s, p in seen if "plainto_tsquery" in s]
    assert len(stmts) == 1
    sql, params = stmts[0]
    assert "drop table" not in sql and "FUEL" not in sql
    assert params["q"] == evil and params["idents"] == ["FUEL-SUR", "FUEL_SUR"]


@needs_db
async def test_and_mode_behaviour_unchanged_default(conn, seeded):
    explicit = await lexical_search(conn, seeded["wa"], "annual leave", index_profile_id=seeded["profile_id"],
                                    lexical_mode="and")
    default = await andm(conn, seeded, "annual leave")
    assert [(c.chunk_id, c.score) for c in explicit] == [(c.chunk_id, c.score) for c in default]


@needs_db
async def test_run_retrievers_threads_lexical_mode(conn, seeded):
    from scribe_intel.retrieval.search import run_retrievers
    q = "Which team owns the ticket for ERR_AUTH_403 in the zzqqxx system?"
    kw = dict(query_vec=None, index_profile_id=seeded["profile_id"], dense_k=0, lexical_k=10)
    assert (await run_retrievers(conn, seeded["wa"], q, **kw)).lexical == []
    snap = await run_retrievers(conn, seeded["wa"], q, lexical_mode="or_rank", **kw)
    assert snap.lexical and snap.lexical[0].doc_name == "api-errors.md"


# ---- profiles in a real database (always rolled back)

@pytest.fixture
def pgconn():
    import psycopg
    c = psycopg.connect(DATABASE_URL_TEST, autocommit=False)
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@needs_db
def test_new_profiles_roundtrip_idempotent_and_immutable(pgconn):
    P.upsert_profiles(pgconn)
    P.upsert_profiles(pgconn)
    rows = {n: (rid, cfg, h) for rid, n, cfg, h in pgconn.execute(
        "select id::text, name, config, config_hash from retrieval_profiles where name = any(%s)",
        ([p.name for p in P.ALL_PROFILES],)).fetchall()}
    assert set(rows) == {p.name for p in P.ALL_PROFILES}
    for p in P.ALL_PROFILES:
        rid, cfg, h = rows[p.name]
        assert rid == P.SEED_IDS[p.name] and cfg == p.config_json and h == p.config_hash
    for p in P.V2_PROFILES:
        assert rows[p.name][1].get("lexical_mode") == p.config_json.get("lexical_mode")
    # A-E rows (seeded by the migration) are untouched
    for name, h in PINNED_HASHES.items():
        assert rows[name][2] == h


@needs_db
def test_mutated_stored_new_profile_raises_immutable(pgconn):
    P.upsert_profiles(pgconn)
    p = P.BY_NAME["hybrid-rrf-rerank-v2"]
    pgconn.execute("delete from retrieval_traces where profile_id = %s::uuid", (P.SEED_IDS[p.name],))
    pgconn.execute("delete from retrieval_profiles where name = %s", (p.name,))
    drifted = {k: v for k, v in p.config_json.items() if k != "lexical_mode"}  # stored row lost the mode
    pgconn.execute("insert into retrieval_profiles (id, name, config, config_hash) values (%s::uuid, %s, %s::jsonb, %s)",
                   (P.SEED_IDS[p.name], p.name, json.dumps(drifted), p.config_hash))
    with pytest.raises(P.ProfileImmutableError):
        P.upsert_profiles(pgconn)
