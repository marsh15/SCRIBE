from uuid import uuid4

from scribe_intel.retrieve_routes import context_json
from scribe_intel.retrieval.context import CharacterSpan, compress_text
from scribe_intel.retrieval.packing import PackedContext, PackedEntry
from scribe_intel.retrieval.types import PackedItem
from scribe_ingest.tokens import count_tokens as real_count_tokens


wc = lambda text: len(text.split())  # noqa: E731


def test_compression_keeps_relevant_whole_sentence_and_original_offsets():
    text = "Annual leave is twenty days. A medical certificate is required after five days. Holiday pay is eighty percent."

    compressed = compress_text(text, "medical certificate", budget_tokens=8, count_tokens=wc)

    expected = "A medical certificate is required after five days."
    assert compressed.text == expected
    assert compressed.kept_spans == (CharacterSpan(text.index(expected), text.index(expected) + len(expected)),)
    assert all(text[span.start:span.end] not in compressed.text for span in compressed.dropped_spans)
    assert text[compressed.kept_spans[0].start:compressed.kept_spans[0].end] == compressed.text
    assert compressed.kept_spans[0].start > 0


def test_soft_newline_stays_inside_the_whole_sentence():
    text = "Employees must\nsubmit the form. Extra context is unrelated."

    compressed = compress_text(text, "submit form", budget_tokens=5, count_tokens=wc)

    expected = "Employees must\nsubmit the form."
    assert compressed.text == expected
    assert text[compressed.kept_spans[0].start:compressed.kept_spans[0].end] == expected


def test_sentence_boundary_includes_closing_quote():
    text = 'The rule says "Forms are required." The next sentence concerns fees.'

    compressed = compress_text(text, "forms required", budget_tokens=6, count_tokens=wc)

    expected = 'The rule says "Forms are required."'
    assert compressed.text == expected
    assert text[compressed.kept_spans[0].start:compressed.kept_spans[0].end] == expected


def test_compression_keeps_verbatim_quotes_and_dropped_ranges_cover_omissions():
    text = "The office closes at 5 p.m. The leave policy grants twenty days annually. Contact HR for exceptions."

    compressed = compress_text(text, "leave policy grants days", budget_tokens=7, count_tokens=wc)

    quote = "The leave policy grants twenty days annually."
    assert quote in compressed.text
    assert any(text[span.start:span.end] == quote for span in compressed.kept_spans)
    reconstructed = [False] * len(text)
    for span in (*compressed.kept_spans, *compressed.dropped_spans):
        for position in range(span.start, span.end):
            reconstructed[position] = True
    assert all(reconstructed)


def test_compression_handles_empty_text_and_budget_edges():
    assert compress_text("", "query", budget_tokens=0, count_tokens=wc).text == ""
    text = "A sentence too large for the budget."
    result = compress_text(text, "sentence", budget_tokens=1, count_tokens=wc)
    assert result.text == "" and result.kept_spans == ()
    assert result.dropped_spans == (CharacterSpan(0, len(text)),)

    empty_query = compress_text("First sentence. Second sentence.", "  ", budget_tokens=2, count_tokens=wc)
    assert empty_query.text == "First sentence."


def test_rendered_separator_tokens_are_included_in_len_budget():
    text = "Alpha. Beta."
    budget = len("Alpha.") + len("Beta.")

    compressed = compress_text(text, "alpha beta", budget_tokens=budget, count_tokens=len)

    assert len(compressed.text) <= budget
    assert compressed.text == "Alpha."


def test_original_whitespace_counts_toward_real_tokenizer_budget():
    text = "First paragraph." + (" " * 1000) + "Second paragraph."
    sentences = ("First paragraph.", "Second paragraph.")
    budget = sum(real_count_tokens(sentence) for sentence in sentences)
    assert real_count_tokens(text) > budget

    compressed = compress_text(
        text, "first paragraph second", budget_tokens=budget, count_tokens=real_count_tokens,
    )

    assert compressed.text != text
    assert real_count_tokens(compressed.text) <= budget


def test_compression_rejects_negative_budget():
    try:
        compress_text("text", "query", budget_tokens=-1, count_tokens=wc)
    except ValueError as exc:
        assert "non-negative" in str(exc)
    else:
        raise AssertionError("negative compression budget was accepted")


def test_dropped_spans_are_serialized_only_for_new_compressed_context():
    item = PackedItem(uuid4(), None, "kept sentence", None, "policy.pdf", 1, uuid4(), [], 2, None)
    entry = PackedEntry(item, kept_spans=[CharacterSpan(12, 25)], dropped_spans=[CharacterSpan(0, 11)])

    compressed = context_json(PackedContext([entry], 2, 10, context_strategy="compressed"))
    legacy = context_json(PackedContext([entry], 2, 10))

    assert compressed["strategy"] == "compressed"
    assert compressed["entries"][0]["kept_spans"] == [{"start": 12, "end": 25}]
    assert compressed["entries"][0]["dropped_spans"] == [{"start": 0, "end": 11}]
    assert "strategy" not in legacy
    assert "kept_spans" not in legacy["entries"][0]
