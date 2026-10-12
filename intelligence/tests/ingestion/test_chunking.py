"""Structural chunker (T11): invariants, Hypothesis properties, corpus/golden quote check."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from collections import defaultdict

import pytest
from _helpers import CORPUS, GOLDEN
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from scribe_eval.normalize import normalize
from scribe_ingest import Block, parse
from scribe_ingest.chunking import STRUCTURAL_V1, ChunkProfile, chunk_document
from scribe_ingest.model import assemble
from scribe_ingest.tokens import TOKENIZER_ID, count_tokens

SMALL = ChunkProfile(name="test-small", child_max_tokens=60, overlap_tokens=10, parent_target_tokens=200)


# ---------------------------------------------------------------- tokens


def test_tokenizer_id_and_counts():
    assert TOKENIZER_ID == "tiktoken:o200k_base"
    assert count_tokens("") == 0
    assert count_tokens("hello world") == 2
    assert count_tokens("<|endoftext|>") > 0  # special strings are plain text


def test_no_network_needed_for_tokenizer():
    code = textwrap.dedent(
        """
        import socket
        def boom(*a, **k):
            raise RuntimeError("network access attempted")
        socket.socket.connect = boom
        socket.getaddrinfo = boom
        socket.create_connection = boom
        from scribe_ingest.tokens import count_tokens
        assert count_tokens("hello world, this needs the o200k_base vocabulary") > 5
        print("ok")
        """
    )
    env = {k: v for k, v in os.environ.items() if k not in ("TIKTOKEN_CACHE_DIR", "DATA_GYM_CACHE_DIR")}
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr


# ---------------------------------------------------------------- helpers


def doc_of(*specs, page_count=None):
    """specs: (type, text, heading_path[, page]) tuples."""
    blocks = []
    for s in specs:
        t, text, path = s[:3]
        page = s[3] if len(s) > 3 else None
        meta = {}
        blocks.append(Block(type=t, text=text, heading_path=list(path), page=page, meta=meta))
    return assemble(blocks, page_count=page_count)


def sentences(n, tag="s"):
    return " ".join(f"This is sentence number {i} of the {tag} paragraph about widgets." for i in range(n))


def span_text(doc, sp):
    return doc.blocks[sp.block_index].text[sp.char_start : sp.char_end]


def check_invariants(doc, cs, profile, *, strict_limit=True):
    kids, parents = cs.children, cs.parents
    assert [c.chunk_index for c in cs.chunks] == list(range(len(cs.chunks)))
    by_index = {c.chunk_index: c for c in cs.chunks}
    cover: dict[int, list[bool]] = {
        b.index: [False] * len(b.text) for b in doc.blocks if not b.furniture
    }
    for c in kids:
        assert c.spans, "every child has >= 1 span"
        assert c.parent_index is not None and by_index[c.parent_index].kind == "parent"
        if strict_limit:
            assert c.token_count <= profile.child_max_tokens, (c.token_count, c.content)
        assert c.token_count == count_tokens(c.content)
        pos = 0
        for sp in c.spans:
            blk = doc.blocks[sp.block_index]
            assert not blk.furniture
            assert 0 <= sp.char_start < sp.char_end <= len(blk.text)
            assert sp.page == blk.page and sp.bbox == blk.bbox
            t = span_text(doc, sp)
            at = c.content.find(t, pos)
            assert at >= 0, "span text must appear in content, in order"
            pos = at + len(t)
            for i in range(sp.char_start, sp.char_end):
                cover[sp.block_index][i] = True
        # a child's blocks all share the child's heading path
        assert {tuple(doc.blocks[s.block_index].heading_path) for s in c.spans} <= {
            tuple(c.heading_path)
        } | {tuple(doc.blocks[s.block_index].heading_path) for s in c.spans if doc.blocks[s.block_index].type == "heading"}
        if len({s.block_index for s in c.spans}) == len(c.spans):
            assert c.content == "\n\n".join(span_text(doc, s) for s in c.spans)
    for bi, flags in cover.items():
        text = doc.blocks[bi].text
        missing = [i for i, f in enumerate(flags) if not f and not text[i].isspace()]
        assert not missing, f"block {bi} chars not covered: {text!r} {missing[:5]}"
    for p in parents:
        mine = [c for c in kids if c.parent_index == p.chunk_index]
        assert mine
        assert [c.chunk_index for c in mine] == list(range(p.chunk_index + 1, p.chunk_index + 1 + len(mine)))
        assert p.token_count == count_tokens(p.content)
    # overlap bound between pieces of the same (non-table/code) split block
    for a, b in zip(kids, kids[1:]):
        for sa in a.spans:
            for sb in b.spans:
                blk = doc.blocks[sa.block_index]
                if sa.block_index != sb.block_index or blk.type in ("table", "code"):
                    continue
                if sb.char_start < sa.char_end and sb.char_start > sa.char_start:
                    ov = blk.text[sb.char_start : sa.char_end]
                    assert count_tokens(ov) <= profile.overlap_tokens, ov


# ---------------------------------------------------------------- hypothesis

WORD = st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=9)


@st.composite
def para_text(draw):
    n = draw(st.integers(1, 60))
    words = [draw(WORD) for _ in range(n)]
    out = []
    for i, w in enumerate(words):
        out.append(w)
        if i < n - 1:
            out.append(draw(st.sampled_from([" ", " ", " ", ". ", ", ", "\n"])).replace(". ", ". "))
    return "".join(out).strip() or "x"


@st.composite
def table_text(draw):
    ncols = draw(st.integers(2, 3))
    nrows = draw(st.integers(1, 14))
    header = "| " + " | ".join(f"h{i}" for i in range(ncols)) + " |"
    sep = "|" + "|".join("---" for _ in range(ncols)) + "|"
    rows = []
    for _ in range(nrows):
        cells = [" ".join(draw(WORD) for _ in range(draw(st.integers(1, 12)))) for _ in range(ncols)]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep, *rows])


@st.composite
def documents(draw):
    specs = []
    path: list[str] = []
    for _ in range(draw(st.integers(1, 14))):
        kind = draw(st.sampled_from(["heading", "paragraph", "paragraph", "list_item", "table", "code"]))
        if kind == "heading":
            level = draw(st.integers(1, 3))
            path = path[: level - 1] + [draw(WORD)]
            specs.append(("heading", path[-1], list(path)))
        elif kind == "table":
            specs.append(("table", draw(table_text()), list(path)))
        elif kind == "code":
            specs.append(("code", "\n".join(draw(para_text()) for _ in range(draw(st.integers(1, 8)))), list(path)))
        else:
            specs.append((kind, draw(para_text()), list(path)))
    return doc_of(*specs)


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(documents())
def test_properties_small_profile(doc):
    cs = chunk_document(doc, SMALL)
    check_invariants(doc, cs, SMALL)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(documents())
def test_properties_default_profile(doc):
    check_invariants(doc, chunk_document(doc, STRUCTURAL_V1), STRUCTURAL_V1)


def test_single_indivisible_token_run_is_still_covered():
    # one 400-char "word": split by characters; limit holds and everything is covered
    doc = doc_of(("paragraph", "q" * 3000, []))
    cs = chunk_document(doc, SMALL)
    check_invariants(doc, cs, SMALL)
    assert len(cs.children) > 1


# ---------------------------------------------------------------- specific behaviours


def test_identical_paragraphs_are_distinct_chunks():
    p = sentences(3, "dup")
    doc = doc_of(
        ("heading", "A", ["A"]), ("paragraph", p, ["A"]),
        ("heading", "B", ["B"]), ("paragraph", p, ["B"]),
    )
    cs = chunk_document(doc, STRUCTURAL_V1)
    dups = [c for c in cs.children if p in c.content]
    assert len(dups) == 2
    assert dups[0].content_hash == dups[1].content_hash or dups[0].heading_path != dups[1].heading_path
    assert {(s.block_index, s.char_start) for s in dups[0].spans}.isdisjoint(
        {(s.block_index, s.char_start) for s in dups[1].spans}
    )
    # same section, identical paragraph twice -> still two spans, no dedupe
    doc2 = doc_of(("paragraph", p, []), ("paragraph", p, []))
    c2 = chunk_document(doc2, ChunkProfile(name="x", child_max_tokens=60, overlap_tokens=5, parent_target_tokens=500))
    spans = [(s.block_index, s.char_start, s.char_end) for c in c2.children for s in c.spans]
    assert (0, 0, len(p)) in spans and (1, 0, len(p)) in spans
    assert len(c2.children) == 2 and c2.children[0].content_hash == c2.children[1].content_hash


def test_small_blocks_pack_and_headings_separate():
    doc = doc_of(
        ("heading", "Policy", ["Policy"]),
        ("paragraph", "One.", ["Policy"]),
        ("paragraph", "Two.", ["Policy"]),
        ("heading", "Leave", ["Policy", "Leave"]),
        ("list_item", "a day", ["Policy", "Leave"]),
        ("list_item", "b day", ["Policy", "Leave"]),
    )
    cs = chunk_document(doc)
    assert [c.content for c in cs.children] == ["Policy\n\nOne.\n\nTwo.", "Leave\n\na day\n\nb day"]
    assert [c.heading_text for c in cs.children] == ["Policy", "Policy > Leave"]
    assert cs.children[1].embed_text == "Policy > Leave\nLeave\n\na day\n\nb day"
    assert len(cs.parents) == 1 and cs.parents[0].chunk_index == 0
    assert [c.chunk_index for c in cs.children] == [1, 2]


def test_bare_heading_carried_into_descendant():
    doc = doc_of(
        ("heading", "Top", ["Top"]),
        ("heading", "Sub", ["Top", "Sub"]),
        ("paragraph", "Body text.", ["Top", "Sub"]),
    )
    cs = chunk_document(doc)
    assert len(cs.children) == 1
    assert cs.children[0].content == "Top\n\nSub\n\nBody text."
    assert cs.children[0].heading_text == "Top > Sub"


def test_no_merge_across_sections_and_parent_boundaries():
    doc = doc_of(
        ("heading", "A", ["A"]), ("paragraph", "alpha", ["A"]),
        ("heading", "B", ["B"]), ("paragraph", "beta", ["B"]),
    )
    cs = chunk_document(doc)
    assert len(cs.parents) == 2
    assert [c.parent_index for c in cs.children] == [0, 2]
    assert [c.chunk_index for c in cs.chunks] == [0, 1, 2, 3]


def test_headingless_document_groups_sequentially():
    prof = ChunkProfile(name="x", child_max_tokens=40, overlap_tokens=5, parent_target_tokens=100)
    doc = doc_of(*[("paragraph", sentences(2, f"p{i}"), []) for i in range(12)])
    cs = chunk_document(doc, prof)
    assert len(cs.parents) > 1
    check_invariants(doc, cs, prof)
    assert all(c.heading_text == "" and c.embed_text == c.content for c in cs.children)


def test_split_block_overlap_and_sentence_boundaries():
    text = sentences(40)
    doc = doc_of(("paragraph", text, []))
    cs = chunk_document(doc, SMALL)
    kids = cs.children
    assert len(kids) > 2
    for c in kids[:-1]:
        assert c.content.rstrip().endswith("."), "pieces end on sentence boundaries"
    ov = [
        (a.spans[0].char_end - b.spans[0].char_start) for a, b in zip(kids, kids[1:])
    ]
    assert all(o > 0 for o in ov), "pieces of a split block overlap"
    check_invariants(doc, cs, SMALL)


def test_no_overlap_between_different_blocks():
    doc = doc_of(*[("paragraph", sentences(4, f"p{i}"), []) for i in range(6)])
    cs = chunk_document(doc, SMALL)
    for a, b in zip(cs.children, cs.children[1:]):
        assert {s.block_index for s in a.spans}.isdisjoint({s.block_index for s in b.spans})


def make_table(nrows=30):
    rows = [f"| item {i} | {sentences(1, 'r')} |" for i in range(nrows)]
    return "\n".join(["| name | note |", "|---|---|", *rows])


def test_table_split_by_rows_repeats_header():
    t = make_table()
    doc = doc_of(("heading", "Prices", ["Prices"]), ("table", t, ["Prices"]))
    cs = chunk_document(doc, SMALL)
    kids = cs.children
    assert len(kids) > 3
    for c in kids:
        assert "| name | note |\n|---|---|\n" in c.content
        for line in c.content.split("\n"):
            assert line.startswith("|") or line in ("Prices", "")
    # every row appears exactly once across pieces (no overlap between row groups)
    for i in range(30):
        assert sum(f"| item {i} |" in c.content for c in kids) == 1
    check_invariants(doc, cs, SMALL)


def test_table_without_separator_uses_first_line_as_header():
    rows = [f"| item {i} | {sentences(1, 'r')} |" for i in range(20)]
    t = "\n".join(["| name | note |", *rows])
    doc = doc_of(("table", t, []))
    cs = chunk_document(doc, SMALL)
    assert len(cs.children) > 2
    assert all(c.content.startswith("| name | note |\n") for c in cs.children)
    check_invariants(doc, cs, SMALL)


def test_oversized_row_split_still_repeats_header():
    big = " ".join(f"word{i}" for i in range(300))
    t = "\n".join(["| name | note |", "|---|---|", "| a | small |", f"| b | {big} |", "| c | tail |"])
    doc = doc_of(("table", t, []))
    cs = chunk_document(doc, SMALL)
    assert len(cs.children) >= 4
    for c in cs.children:
        assert c.content.startswith("| name | note |\n|---|---|\n")
    assert sum("word299" in c.content for c in cs.children) == 1
    check_invariants(doc, cs, SMALL)


def test_code_block_split_by_lines():
    code = "\n".join(f"    result_{i} = compute({i}, {i * 2})  # step" for i in range(60))
    doc = doc_of(("code", code, []))
    cs = chunk_document(doc, SMALL)
    assert len(cs.children) > 2
    for c in cs.children:
        for line in c.content.split("\n"):
            assert line.startswith("    result_") and line.endswith("# step")
    check_invariants(doc, cs, SMALL)


def test_pages_derived_from_spans():
    doc = doc_of(
        ("paragraph", "on page two", [], 2), ("paragraph", "on page three", [], 3),
    )
    c = chunk_document(doc).children[0]
    assert (c.page_start, c.page_end) == (2, 3)
    nopage = chunk_document(doc_of(("paragraph", "no pages", []))).children[0]
    assert nopage.page_start is None and nopage.page_end is None


def test_furniture_excluded():
    blocks = [
        Block(type="paragraph", text="PAGE HEADER", furniture=True),
        Block(type="paragraph", text="real body"),
    ]
    doc = assemble(blocks)
    cs = chunk_document(doc)
    assert "PAGE HEADER" not in "".join(c.content for c in cs.chunks)
    assert cs.children[0].spans[0].block_index == 1


def test_config_hash_stability():
    assert STRUCTURAL_V1.config_hash == ChunkProfile(name="structural-v1").config_hash
    assert STRUCTURAL_V1.config_hash != ChunkProfile(name="structural-v1", overlap_tokens=49).config_hash
    assert STRUCTURAL_V1.config_hash != ChunkProfile(name="other").config_hash
    assert len(STRUCTURAL_V1.config_hash) == 64
    # pinned: a change here is a new chunking profile and needs a new profile row
    assert STRUCTURAL_V1.config_hash == "601add4176f2afeb02cdbbf4236584d980af2d68904348d97caab037458c3b2d"
    assert (STRUCTURAL_V1.child_max_tokens, STRUCTURAL_V1.overlap_tokens, STRUCTURAL_V1.parent_target_tokens) == (384, 48, 1536)
    assert STRUCTURAL_V1.tokenizer_id == TOKENIZER_ID


def test_chunking_is_deterministic():
    doc = doc_of(("heading", "H", ["H"]), ("paragraph", sentences(50), ["H"]), ("table", make_table(), ["H"]))
    a, b = chunk_document(doc, SMALL), chunk_document(doc, SMALL)
    assert [(c.content, c.spans) for c in a.chunks] == [(c.content, c.spans) for c in b.chunks]


# ---------------------------------------------------------------- real corpus + golden quotes

MIMES = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".csv": "text/csv",
}


def _parseable(name: str):
    ext = "." + name.rsplit(".", 1)[1]
    if name == "facility-safety-notice-scanned.pdf":
        return None  # no text layer: OCR is T21
    try:
        return parse((CORPUS / name).read_bytes(), MIMES[ext])
    except Exception as e:  # unsupported (parser not registered) -> skip
        if getattr(e, "code", "") == "UNSUPPORTED_FORMAT":
            return None
        raise


def _corpus_docs():
    return sorted(p.name for p in CORPUS.iterdir() if p.suffix in MIMES)


@pytest.fixture(scope="module")
def corpus_chunks():
    out = {}
    for name in _corpus_docs():
        doc = _parseable(name)
        if doc is not None:
            out[name] = (doc, chunk_document(doc, STRUCTURAL_V1))
    return out


def test_corpus_invariants_and_stats(corpus_chunks, capsys):
    assert len(corpus_chunks) >= 10
    lines = []
    for name, (doc, cs) in corpus_chunks.items():
        check_invariants(doc, cs, STRUCTURAL_V1)
        toks = [c.token_count for c in cs.children]
        lines.append(
            f"{name:42s} parents={len(cs.parents):3d} children={len(toks):3d} "
            f"mean_tokens={sum(toks) / len(toks):6.1f} max_tokens={max(toks)}"
        )
    with capsys.disabled():
        print("\n" + "\n".join(lines))


def test_golden_quotes_survive_chunking(corpus_chunks, capsys):
    total = defaultdict(int)
    split = defaultdict(list)
    not_in_block = defaultdict(list)  # quote is not in any single block (parser representation)
    seen = set()
    for line in GOLDEN.read_text().splitlines():
        case = json.loads(line)
        for ev in case["evidence"]:
            doc_name = ev["doc"]
            if doc_name not in corpus_chunks or (doc_name, ev["quote"]) in seen:
                continue
            seen.add((doc_name, ev["quote"]))
            ext = doc_name.rsplit(".", 1)[1]
            total[ext] += 1
            q = normalize(ev["quote"])
            doc, cs = corpus_chunks[doc_name]
            if not any(q in normalize(b.text) for b in doc.chunkable_blocks):
                not_in_block[ext].append((case["id"], doc_name))
                continue
            if not any(q in normalize(c.content) for c in cs.children):
                split[ext].append((case["id"], doc_name, ev["quote"]))
    with capsys.disabled():
        print("\ngolden quotes checked / split across chunks, by type:")
        for ext in sorted(total):
            print(f"  {ext:5s} {total[ext]:3d} / {len(split.get(ext, []))}")
        print("quotes not present in any single parsed block (not a chunking issue):",
              {k: len(v) for k, v in not_in_block.items()})
        for ext, items in split.items():
            for it in items:
                print("   SPLIT", it)
    assert sum(total.values()) >= 70
    # only CSV (raw-row quotes vs rendered row blocks) may lack a single-block home
    assert set(not_in_block) <= {"csv"}, dict(not_in_block)
    assert not any(split.values()), dict(split)
