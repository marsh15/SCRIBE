from __future__ import annotations

from pathlib import Path

from scribe_ingest import parse
from scribe_ingest.chunking import Chunk, chunk_document
from scribe_ingest.version_diff import align_sections
from scribe_ingest.value_extract import pair_value_changes


def chunk(chunk_id: int, path: tuple[str, ...], content: str) -> Chunk:
    return Chunk("child", chunk_id, content, list(path), 1, [])


def test_normalized_exact_path_is_unchanged_and_keeps_chunk_ids():
    old = chunk(11, ("Annual Leave", "Carry-over"), "Up to 5 days may carry over.")
    new = chunk(27, (" annual   leave ", "CARRY-OVER"), "Up to 5 days may carry over.")

    [change] = align_sections([old], [new])

    assert (change.kind, change.old_id, change.new_id) == ("unchanged", 11, 27)


def test_exact_path_with_changed_text_is_changed():
    old = chunk(2, ("Leave", "Annual"), "Employees receive 24 days.")
    new = chunk(9, ("Leave", "Annual"), "Employees receive 27 days.")

    [change] = align_sections([old], [new])

    assert change.kind == "changed"
    assert (change.old_id, change.new_id) == (2, 9)


def test_equal_content_at_new_path_is_a_move():
    old = chunk(4, ("2024 policy", "Annual leave"), "Full-time employees receive 24 working days.")
    new = chunk(15, ("2026 policy", "Annual leave"), "Full-time employees receive 24 working days.")

    [change] = align_sections([old], [new])

    assert change.kind == "moved"
    assert (change.old_id, change.new_id) == (4, 15)


def test_similar_text_under_renamed_path_is_changed():
    old = chunk(1, ("Old handbook", "Sick leave"), "A medical certificate is required after 3 consecutive working days.")
    new = chunk(8, ("Current policy", "Sick leave"), "A medical certificate is required after 5 consecutive working days.")

    [change] = align_sections([old], [new])

    assert change.kind == "changed"
    assert change.similarity is not None and change.similarity >= 0.6


def test_additions_and_removals_keep_their_original_ids():
    old = [chunk(3, ("A",), "Old section remains."), chunk(5, ("B",), "Incident response telephone tree.")]
    new = [chunk(13, ("A",), "Old section remains."), chunk(17, ("C",), "Updated parental leave allowance.")]

    changes = align_sections(old, new)

    assert [(item.kind, item.old_id, item.new_id) for item in changes] == [
        ("unchanged", 3, 13),
        ("added", None, 17),
        ("removed", 5, None),
    ]


def test_duplicate_paths_pair_one_to_one_in_document_order():
    old = [chunk(2, ("Same",), "First copy."), chunk(4, ("Same",), "Second copy.")]
    new = [chunk(12, ("Same",), "First copy."), chunk(14, ("Same",), "Edited second copy.")]

    changes = align_sections(old, new)

    assert [(item.kind, item.old_id, item.new_id) for item in changes] == [
        ("unchanged", 2, 12),
        ("changed", 4, 14),
    ]


def test_northwind_versions_parse_and_align_known_policy_changes():
    root = Path(__file__).resolve().parents[3]
    corpus = root / "fixtures" / "corpus"
    old_doc = parse((corpus / "hr-leave-policy-2024.pdf").read_bytes(), "application/pdf")
    new_doc = parse((corpus / "hr-leave-policy-2026.pdf").read_bytes(), "application/pdf")
    old_chunks = chunk_document(old_doc).children
    new_chunks = chunk_document(new_doc).children

    changes = align_sections(old_chunks, new_chunks)

    expected = next(
        item for item in changes
        if item.old_text and "Full-time employees are entitled to 24 working days" in item.old_text
    )
    assert expected.kind == "changed"
    assert expected.new_text and "Full-time employees are entitled to 27 working days" in expected.new_text
    assert {item.old_id for item in changes if item.old_id is not None} <= {c.chunk_index for c in old_chunks}
    assert {item.new_id for item in changes if item.new_id is not None} <= {c.chunk_index for c in new_chunks}

    old_text = "\n".join(chunk.content for chunk in old_chunks)
    new_text = "\n".join(chunk.content for chunk in new_chunks)
    value_pairs = {
        (item.old_value, item.new_value)
        for item in pair_value_changes(old_text, new_text)
    }
    assert ("24 working days", "27 working days") in value_pairs
    assert ("5 unused days", "8 unused days") in value_pairs
    assert ("31 March", "30 June") in value_pairs
    assert ("3 consecutive working days", "5 consecutive working days") in value_pairs
    assert ("12 weeks", "16 weeks") in value_pairs
    assert ("80%", "100%") in value_pairs
    assert ("3 working days", "5 working days") in value_pairs
    assert ("30 days", "60 days") in value_pairs
    assert ("14 calendar days", "10 working days") in value_pairs
