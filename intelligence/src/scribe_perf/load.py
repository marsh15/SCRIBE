"""Bulk loader: relational rows + text via COPY, then vectors via COPY, then HNSW builds (timed)."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import psycopg

from .pgdb import HALF_TABLE, HNSW_INDEX, require_throwaway, reset_data, timed
from .synth import (DEFAULT_FRACTIONS, CHUNK_NOISE, DIMS, DOC_NOISE, QUERY_NOISE, TOPIC_QUERY_NOISE, chunk_text, det_uuid, gauss_unit,
                    make_vocab, normalize, perturb, split_counts, vec_text, zipf_cum_weights)

CHUNKS_PER_DOC = (10, 30)
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 64
MAINTENANCE_WORK_MEM = "512MB"


@dataclass
class DocPlan:
    file_id: int
    version_id: object
    generation_id: object
    parent_id: object
    child_ids: list
    topic: int


@dataclass
class WorkspacePlan:
    index: int
    id: object
    n_children: int
    docs: list[DocPlan] = field(default_factory=list)
    n_topics: int = 1


@dataclass
class Loaded:
    workspaces: list[WorkspacePlan]
    index_profile_id: str
    embedding_profile_id: str
    queries: dict[str, list[list[float]]]  # workspace id -> query vectors
    build_s: dict[str, float]
    seed: int


def plan_workspaces(total: int, seed: int, fractions=DEFAULT_FRACTIONS) -> list[WorkspacePlan]:
    ids = random.Random(f"ids:{seed}")
    next_file = 1
    plans: list[WorkspacePlan] = []
    for i, n in enumerate(split_counts(total, fractions)):
        rng = random.Random(f"plan:{seed}:{i}")
        wp = WorkspacePlan(index=i, id=det_uuid(ids), n_children=n)
        remaining = n
        while remaining > 0:
            k = min(remaining, rng.randint(*CHUNKS_PER_DOC))
            wp.docs.append(DocPlan(next_file, det_uuid(ids), det_uuid(ids), det_uuid(ids),
                                   [det_uuid(ids) for _ in range(k)], 0))
            next_file += 1
            remaining -= k
        plans.append(wp)
    # Topics are SHARED across workspaces (real workspaces overlap in subject matter), so a small workspace's nearest
    # neighbours in the global HNSW graph are mostly other workspaces' chunks: that is what makes the workspace filter bite.
    n_topics = max(1, sum(len(w.docs) for w in plans) // 10)
    trng = random.Random(f"topic-assign:{seed}")
    for wp in plans:
        wp.n_topics = n_topics
        for d in wp.docs:
            d.topic = trng.randrange(n_topics)
    return plans


def _topics(seed: int, n_topics: int) -> list[list[float]]:
    rng = random.Random(f"topics:{seed}")
    return [gauss_unit(rng) for _ in range(n_topics)]


def _doc_center(topics, d: DocPlan, seed: int, wp: WorkspacePlan, doc_idx: int) -> list[float]:
    return perturb(topics[d.topic], DOC_NOISE, random.Random(f"doc:{seed}:{wp.index}:{doc_idx}"))


def load_relational(conn: psycopg.Connection, plans: list[WorkspacePlan], seed: int) -> tuple[str, str]:
    row = conn.execute("select id, embedding_profile_id from index_profiles where name = 'structural-v1'").fetchone()
    if row is None:
        raise RuntimeError("seeded index profile 'structural-v1' missing (schema replay incomplete?)")
    profile_id, emb_profile = str(row[0]), str(row[1])
    vocab = make_vocab(seed)
    cum = zipf_cum_weights(len(vocab))
    with conn.cursor() as cur:
        with cur.copy("copy workspaces (id, kind, owner_user_id) from stdin") as cp:
            for wp in plans:
                cp.write_row((str(wp.id), "perf", f"perf-{seed}-{wp.index}"))
        with cur.copy("copy files (id, name, type, size, user_id, workspace_id, status) from stdin") as cp:
            for wp in plans:
                for d in wp.docs:
                    cp.write_row((d.file_id, f"doc-{d.file_id}.md", "text/markdown", 1, "perf", str(wp.id), "ready"))
        with cur.copy("copy source_versions (id, source_id, workspace_id, version_number, upload_state) from stdin") as cp:
            for wp in plans:
                for d in wp.docs:
                    cp.write_row((str(d.version_id), d.file_id, str(wp.id), 1, "stored"))
        with cur.copy("copy index_generations (id, source_id, source_version_id, index_profile_id, workspace_id, status, "
                      "expected_chunks, embedded_chunks) from stdin") as cp:
            for wp in plans:
                for d in wp.docs:
                    cp.write_row((str(d.generation_id), d.file_id, str(d.version_id), profile_id, str(wp.id), "ACTIVE",
                                  len(d.child_ids), len(d.child_ids)))
        with cur.copy("copy active_indexes (source_id, index_profile_id, generation_id, workspace_id) from stdin") as cp:
            for wp in plans:
                for d in wp.docs:
                    cp.write_row((d.file_id, profile_id, str(d.generation_id), str(wp.id)))
        with cur.copy("copy chunks (id, generation_id, source_version_id, workspace_id, parent_id, chunk_index, content, "
                      "heading_text, content_hash, token_count) from stdin") as cp:
            for wp in plans:
                for j, d in enumerate(wp.docs):
                    rng = random.Random(f"text:{seed}:{wp.index}:{j}")
                    cp.write_row((str(d.parent_id), str(d.generation_id), str(d.version_id), str(wp.id), None, 0,
                                  f"parent of doc {d.file_id}", None, f"p{d.parent_id}", 0))
                    for c, cid in enumerate(d.child_ids, 1):
                        cp.write_row((str(cid), str(d.generation_id), str(d.version_id), str(wp.id), str(d.parent_id), c,
                                      chunk_text(rng, vocab, cum), " ".join(rng.choices(vocab[:200], k=3)),
                                      f"h{cid}", 80))
    return profile_id, emb_profile


def load_embeddings(conn: psycopg.Connection, plans: list[WorkspacePlan], seed: int, emb_profile: str
                    ) -> dict[str, list[list[float]]]:
    """Second pass: regenerate each document's vectors deterministically (no spooling to disk).
    Returns the query vectors per workspace (perturbed document centres)."""
    queries: dict[str, list[list[float]]] = {}
    topics = _topics(seed, plans[0].n_topics)
    with conn.cursor() as cur:
        with cur.copy("copy chunk_embeddings_1536 (chunk_id, workspace_id, embedding_profile_id, embedding) from stdin") as cp:
            for wp in plans:
                qdocs = set(random.Random(f"qdocs:{seed}:{wp.index}").sample(range(len(wp.docs)), min(len(wp.docs), 100)))
                qs: list[tuple[int, list[float]]] = []
                for j, d in enumerate(wp.docs):
                    center = _doc_center(topics, d, seed, wp, j)
                    rng = random.Random(f"vec:{seed}:{wp.index}:{j}")
                    for cid in d.child_ids:
                        cp.write_row((str(cid), str(wp.id), emb_profile, vec_text(perturb(center, CHUNK_NOISE, rng))))
                    if j in qdocs:
                        # alternate document-level and topic-level queries (the latter have no single dominant document)
                        base = center if len(qs) % 2 == 0 else topics[d.topic]
                        qs.append((j, perturb(base, QUERY_NOISE if len(qs) % 2 == 0 else TOPIC_QUERY_NOISE,
                                              random.Random(f"q:{seed}:{wp.index}:{j}"))))
                queries[str(wp.id)] = [q for _, q in sorted(qs)]
    return queries


def build_indexes(conn: psycopg.Connection) -> dict[str, float]:
    """Drop-and-rebuild so build time is measured with the same definition as drizzle/0010, then the halfvec variant."""
    out: dict[str, float] = {}
    conn.execute(f"set maintenance_work_mem = '{MAINTENANCE_WORK_MEM}'")
    out["hnsw_vector_s"] = timed(conn, f'create index "{HNSW_INDEX}" on chunk_embeddings_1536 using hnsw '
                                       f'(embedding vector_cosine_ops) with (m={HNSW_M}, ef_construction={HNSW_EF_CONSTRUCTION})')
    out["halfvec_table_s"] = timed(conn, f"create table {HALF_TABLE} as select chunk_id, workspace_id, "
                                         f"embedding::halfvec({DIMS}) as embedding from chunk_embeddings_1536")
    conn.execute(f"alter table {HALF_TABLE} add primary key (chunk_id)")
    conn.execute(f"create index {HALF_TABLE}_workspace on {HALF_TABLE} (workspace_id)")
    out["hnsw_halfvec_s"] = timed(conn, f"create index {HALF_TABLE}_hnsw on {HALF_TABLE} using hnsw "
                                        f"(embedding halfvec_cosine_ops) with (m={HNSW_M}, ef_construction={HNSW_EF_CONSTRUCTION})")
    for t in ("workspaces", "files", "source_versions", "index_generations", "active_indexes", "chunks",
              "chunk_embeddings_1536", HALF_TABLE):
        conn.execute(f"analyze {t}")
    return out


def load_scale(url: str, total: int, seed: int, fractions=DEFAULT_FRACTIONS) -> Loaded:
    """Reset the perf DB data, load `total` child chunks, build indexes. Caller owns disk checks."""
    require_throwaway(url)
    plans = plan_workspaces(total, seed, fractions)
    with psycopg.connect(url, autocommit=True) as conn:
        reset_data(conn)
        conn.execute(f"drop index if exists {HNSW_INDEX}")
        profile_id, emb_profile = load_relational(conn, plans, seed)
        queries = load_embeddings(conn, plans, seed, emb_profile)
        build = build_indexes(conn)
    return Loaded(plans, profile_id, emb_profile, queries, build, seed)
