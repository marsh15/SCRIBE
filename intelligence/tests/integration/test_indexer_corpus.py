"""T12: whole-corpus ingest with the FAKE embedder against a real pgvector database."""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path

import psycopg
import pytest
from _db import CORPUS, GOLDEN, aconnect, new_workspace
from conftest import DATABASE_URL_TEST, needs_db
from scribe_eval.dataset import Case
from scribe_eval.ingest_corpus import EXPECTED_FAILURES, evaluate, format_table, ingest_corpus, list_corpus
from scribe_eval.metrics import RankedItem, match_matrix
from scribe_eval.normalize import normalize
from scribe_ingest.embedding import FakeEmbedder

pytestmark = needs_db


@pytest.fixture(scope="module")
def ingested():
    async def go():
        conn = await aconnect(DATABASE_URL_TEST)
        try:
            wid = await new_workspace(conn)
            emb = FakeEmbedder()
            results = await ingest_corpus(conn, wid, list_corpus(CORPUS), lambda n: (CORPUS / n).read_bytes(), emb)
            return wid, results, emb
        finally:
            await conn.close()

    wid, results, emb = asyncio.run(go())
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
        yield {"wid": wid, "results": dict(results), "list": results, "conn": c, "emb": emb}


def q(ing, sql, *params):
    return ing["conn"].execute(sql, params).fetchall()


def test_every_document_active_and_fully_embedded(ingested):
    print("\nFAKE-EMBEDDER corpus ingest\n" + format_table(ingested["list"]))
    assert evaluate(ingested["list"]) == []
    for name, r in ingested["results"].items():
        if name in EXPECTED_FAILURES:
            assert r.status == "FAILED" and r.error_code == "OCR_NOT_AVAILABLE"
            assert r.message == "scanned pages need OCR (T21)"
        else:
            assert r.status == "ACTIVE" and r.embedded == r.expected == r.children > 0


def test_active_indexes_and_file_state(ingested):
    rows = q(ingested, "select f.name, f.status, f.active_version_id, f.desired_version_id, ai.generation_id, "
                       "g.status from files f left join active_indexes ai on ai.source_id=f.id "
                       "left join index_generations g on g.id=ai.generation_id where f.workspace_id=%s", ingested["wid"])
    assert len(rows) == len(ingested["results"])
    for name, status, active, desired, gen, gstatus in rows:
        if name in EXPECTED_FAILURES:
            assert status == "failed" and active is None and gen is None
        else:
            assert status == "ready" and active == desired and gen and gstatus == "ACTIVE"
    text_bytes = q(ingested, "select count(*) from files where workspace_id=%s and status='ready' and text_bytes > 0",
                   ingested["wid"])[0][0]
    assert text_bytes == len(rows) - 1


def test_failed_generation_recorded_for_scanned_pdf(ingested):
    (status, code), = q(ingested, "select g.status, g.error_code from index_generations g join files f on f.id=g.source_id "
                                  "where f.workspace_id=%s and f.name like '%%scanned.pdf'", ingested["wid"])
    assert (status, code) == ("FAILED", "OCR_NOT_AVAILABLE")


def test_only_children_have_embeddings(ingested):
    wid = ingested["wid"]
    assert q(ingested, "select count(*) from chunk_embeddings_1536 e join chunks c on c.id=e.chunk_id "
                       "where c.workspace_id=%s and c.parent_id is null", wid)[0][0] == 0
    assert q(ingested, "select count(*) from chunks c where c.workspace_id=%s and c.parent_id is not null and "
                       "not exists (select 1 from chunk_embeddings_1536 e where e.chunk_id=c.id)", wid)[0][0] == 0
    n_children = q(ingested, "select count(*) from chunks where workspace_id=%s and parent_id is not null", wid)[0][0]
    assert n_children == sum(r.children for r in ingested["results"].values())
    # embeddings carry the index profile's embedding profile and are unit length
    ids = {r[0] for r in q(ingested, "select distinct embedding_profile_id::text from chunk_embeddings_1536 "
                                     "where workspace_id=%s", wid)}
    assert ids == {"e0000000-0000-4000-8000-000000000002"}
    norm = q(ingested, "select max(abs(vector_norm(embedding) - 1)) from chunk_embeddings_1536 where workspace_id=%s", wid)[0][0]
    assert norm < 1e-4


def test_spans_reference_real_blocks_and_reproduce_text(ingested):
    rows = q(ingested, """select c.id, c.content, c.parent_id is null, b.text, s.char_start, s.char_end, b.source_version_id,
                                 c.source_version_id
                          from chunk_source_spans s join chunks c on c.id=s.chunk_id
                          join document_blocks b on b.id=s.block_id where c.workspace_id=%s""", ingested["wid"])
    assert rows
    bad = []
    for cid, content, is_parent, btext, cs, ce, bver, cver in rows:
        assert bver == cver
        assert 0 <= cs <= ce <= len(btext)
        if btext[cs:ce] not in content:
            bad.append((cid, btext[cs:ce][:40]))
    assert not bad, f"{len(bad)} spans whose block text is not contained in the chunk: {bad[:3]}"
    # every chunk has at least one span
    assert q(ingested, "select count(*) from chunks c where c.workspace_id=%s and not exists "
                       "(select 1 from chunk_source_spans s where s.chunk_id=c.id)", ingested["wid"])[0][0] == 0


def test_chunk_index_shared_sequence(ingested):
    rows = q(ingested, "select generation_id, count(*), min(chunk_index), max(chunk_index) from chunks "
                       "where workspace_id=%s group by 1", ingested["wid"])
    assert all(mn == 0 and mx == n - 1 for _, n, mn, mx in rows)


def test_golden_evidence_contained_in_child_chunks(ingested, capsys):
    cases = [Case.model_validate(json.loads(l)) for l in GOLDEN.read_text().splitlines() if l.strip()]
    children = defaultdict(list)
    for name, content in q(ingested, "select f.name, c.content from chunks c join index_generations g on g.id=c.generation_id "
                                     "join files f on f.id=g.source_id where c.workspace_id=%s and c.parent_id is not null "
                                     "and g.status='ACTIVE'", ingested["wid"]):
        children[name].append(RankedItem(chunk_id=None, text=content, doc=name))
    canon = {}
    ok = split = missed = 0
    split_list, missed_list = [], []
    for case in cases:
        for ev in case.evidence:
            if ev.doc in EXPECTED_FAILURES or ev.doc not in children:
                continue
            one = case.model_copy(update={"evidence": [ev]})
            if any(match_matrix(one, children[ev.doc])):
                ok += 1
                continue
            if ev.doc not in canon:
                from scribe_ingest import parse
                from scribe_eval.ingest_corpus import MIMES
                p = CORPUS / ev.doc
                canon[ev.doc] = normalize(parse(p.read_bytes(), MIMES[p.suffix], name=p.name).text)
            if normalize(ev.quote) in canon[ev.doc]:
                split += 1; split_list.append((case.id, ev.doc))
            else:
                missed += 1; missed_list.append((case.id, ev.doc, ev.quote[:50]))
    with capsys.disabled():
        print(f"\nevidence entries: contained={ok} split_across_chunks={split} not_in_document={missed}")
        if split_list: print("  split:", split_list)
        if missed_list: print("  missed:", missed_list)
    assert missed == 0
    assert ok + split > 70
    assert split == 0, f"quotes split across child chunks: {split_list}"
