"""T13: lexical retriever (fts_en + fts_simple, D10)."""
from __future__ import annotations

import pytest
from conftest import DATABASE_URL_TEST, needs_db
from _t13 import conn_for, corpus_workspace, seed_sync
from scribe_intel.retrieval.lexical import lexical_search, lexical_sql, separator_variant

pytestmark = needs_db


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


async def lex(conn, ws, q, **kw):
    return await lexical_search(conn, ws["wa"] if "wa" in ws else ws["wid"], q, index_profile_id=ws["profile_id"], **kw)


def names(cands):
    return [c.doc_name for c in cands]


# ---- identifiers (the tokenization findings are asserted, not assumed)

async def test_simple_config_tokenizes_identifiers_as_documented(conn):
    cur = await conn.execute(
        "select to_tsvector('simple','ERR_AUTH_403 INV-2047 NW-COLD-04')::text, "
        "websearch_to_tsquery('simple','ERR_AUTH_403')::text, websearch_to_tsquery('simple','INV-2047')::text, "
        "websearch_to_tsquery('simple','NW-COLD-04')::text, websearch_to_tsquery('simple','INV_2047')::text")
    vec, q1, q2, q3, q4 = await cur.fetchone()
    assert "'err':1" in vec and "'auth':2" in vec and "'403':3" in vec          # underscores split
    assert "'inv':4" in vec and "'-2047':5" in vec                              # hyphen+digits = signed int
    assert "'nw-cold':6" in vec and "'nw':7" in vec                             # compound + parts
    assert q1 == "'err' <-> 'auth' <-> '403'"                                   # query side is a phrase
    assert q2 == "'inv' <-> '-2047'"
    assert q3 == "'nw-cold' <-> 'nw' <-> 'cold' <-> '04'"
    assert q4 == "'inv' <-> '2047'"                                              # why the variant is needed


async def test_identifiers_hit_top3_in_toy_workspace(conn, seeded):
    for q in ("ERR_AUTH_403", "INV-2047", "NW-COLD-04", "err_auth_403", "inv-2047"):
        res = await lex(conn, seeded, q)
        assert res and res[0].doc_name == "api-errors.md", (q, names(res))


async def test_identifiers_hit_top3_in_full_corpus(conn, corpus):
    for q, doc, needle in [("ERR_AUTH_403", "api-spec.md", "ERR_AUTH_403"), ("INV-2047", "api-spec.md", "INV-2047"),
                           ("SKU NW-COLD-04", "ops-manual.md", "NW-COLD-04"), ("NW-COLD-04", "ops-manual.md", "NW-COLD-04")]:
        res = await lex(conn, corpus, q)
        top3 = res[:3]
        assert any(c.doc_name == doc and needle in c.content for c in top3), (q, [(c.doc_name, c.score) for c in res[:5]])


async def test_separator_variants_match_the_other_spelling(conn, seeded):
    assert separator_variant("INV-2047 closed") == "INV_2047 closed"
    assert separator_variant("ERR_AUTH_403") == "ERR-AUTH-403"
    assert separator_variant("plain words") is None and separator_variant("a-b_c") is None
    # stored spelling is ERR_AUTH_403 / INV-2047; the other spelling still finds it
    for q in ("ERR-AUTH-403", "INV_2047"):
        res = await lex(conn, seeded, q)
        assert res and res[0].doc_name == "api-errors.md", q


async def test_bare_number_does_not_match_signed_token_known_gap(conn, seeded):
    """Documented limitation: stored 'INV-2047' holds the lexeme '-2047'; the bare query '2047' does not hit."""
    assert await lex(conn, seeded, "2047") == []


# ---- phrases, stemming

async def test_quoted_phrase_uses_phrase_matching(conn, seeded):
    scattered = await lex(conn, seeded, "annual leave")
    assert {"hr.md", "scatter.md"} <= set(names(scattered))  # both words, any position
    assert names(scattered)[0] == "hr.md"                     # adjacent words rank first
    phrase = await lex(conn, seeded, '"annual leave"')
    assert names(phrase) == ["hr.md"]                         # scattered words no longer match
    assert await lex(conn, seeded, '"leave annual"') == []    # order matters
    mixed = await lex(conn, seeded, '"annual leave" days')
    assert names(mixed) == ["hr.md"]


async def test_quoted_phrase_beats_scattered_words_in_rank(conn, seeded):
    res = await lex(conn, seeded, "annual leave")
    by = {c.doc_name: c.score for c in res}
    assert by["hr.md"] > by["scatter.md"]


async def test_english_stemming_leaves_finds_leave(conn, seeded):
    res = await lex(conn, seeded, "leaves")
    assert {"hr.md", "stem.md", "scatter.md"} <= set(names(res))
    res = await lex(conn, seeded, "taking")
    assert "stem.md" in names(res)  # take -> tak, taking -> take


# ---- robustness

@pytest.mark.parametrize("q", [
    "'; drop table chunks; --", '" unbalanced quote', "a & b | !c <-> d", "\\", "%s %(q)s {} %%", "x\x00y",
    "(((", "-", "- -foo", "or or or", "ünïcödé 日本語 émoji 😀", "'" * 50, "a" * 2000, "<script>alert(1)</script>"])
async def test_hostile_queries_are_safe(conn, seeded, q):
    res = await lex(conn, seeded, q)
    assert isinstance(res, list)
    cur = await conn.execute("select count(*) from chunks where workspace_id=%s", (seeded["wa"],))
    assert (await cur.fetchone())[0] > 0  # nothing was dropped


@pytest.mark.parametrize("q", ["", "   ", "the", "the of and a", "\x00"])
async def test_empty_and_stopword_only_queries_return_nothing(conn, seeded, q):
    assert await lex(conn, seeded, q) == []


async def test_ranking_shape_and_determinism(conn, seeded):
    a = await lex(conn, seeded, "error codes tickets", k=40)
    b = await lex(conn, seeded, "error codes tickets", k=40)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b] and len(a) >= 3
    assert [c.rank for c in a] == list(range(1, len(a) + 1))
    assert all(c.method == "lexical" for c in a)
    assert [c.score for c in a] == sorted((c.score for c in a), reverse=True)
    ties = [(x, y) for x, y in zip(a, a[1:]) if x.score == y.score]
    assert ties and all(str(x.chunk_id) < str(y.chunk_id) for x, y in ties)  # tiebreak: chunk id
    assert len(await lex(conn, seeded, "error codes tickets", k=2)) == 2


async def test_source_ids_filter(conn, seeded):
    res = await lex(conn, seeded, "leave", source_ids=[seeded["a_ids"]["stem.md"]])
    assert names(res) == ["stem.md"]
    assert await lex(conn, seeded, "leave", source_ids=[]) == []


def test_sql_uses_one_score_expression_and_filters_before_limit():
    sql = lexical_sql(True)
    assert "greatest(" in sql and sql.index("g.status = 'ACTIVE'") < sql.index("limit") \
        and sql.index("c.workspace_id = %(w)s") < sql.index("limit") and "deleted_at is null" in sql
    assert "ts_rank_cd(c.fts_en" in sql and "ts_rank_cd('{0.4,0.4,0.4,0.4}'::float4[], c.fts_simple" in sql
    assert "ilike" not in sql.lower() and "trgm" not in sql.lower()
