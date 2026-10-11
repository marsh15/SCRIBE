"""T18: retry taxonomy and backoff (pure, no database)."""
import random

import psycopg
import pytest
from scribe_ingest.blob import BlobDownloadError
from scribe_ingest.embedding import EmbeddingError
from scribe_ingest.errors import IngestError
from scribe_ingest.indexer import IndexerError
from scribe_intel.ingest_steps import backoff_minutes, classify, decide, error_from_exception


class FixedRng:
    def __init__(self, v): self.v = v
    def uniform(self, a, b): return a + (b - a) * self.v


@pytest.mark.parametrize("code", ["RATE_LIMITED", "TRANSIENT_PROVIDER", "DATABASE_UNAVAILABLE", "STORAGE_UNAVAILABLE"])
def test_transient_codes_retry_up_to_five_attempts(code):
    assert classify(code) == "transient"
    for attempts in (1, 2, 3, 4):
        assert decide(code, attempts, FixedRng(0)).action == "retry"
    assert decide(code, 5, FixedRng(0)).action == "fail"


@pytest.mark.parametrize("code", ["INVALID_DOCUMENT", "UNSUPPORTED_FORMAT", "RESOURCE_LIMIT", "EMPTY_EXTRACTION"])
def test_permanent_codes_fail_fast(code):
    assert classify(code) == "permanent"
    assert decide(code, 1, FixedRng(0)).action == "fail"


def test_unknown_codes_get_two_attempts():
    assert classify("INTERNAL") == "unknown"
    assert decide("INTERNAL", 1).action == "retry"
    assert decide("INTERNAL", 2).action == "fail"


def test_backoff_is_two_to_the_n_minutes_plus_bounded_jitter():
    assert backoff_minutes(1, FixedRng(0)) == 2
    assert backoff_minutes(2, FixedRng(0)) == 4
    assert backoff_minutes(3, FixedRng(0)) == 8
    assert backoff_minutes(2, FixedRng(1)) == pytest.approx(5.0)  # +25% max
    assert backoff_minutes(9, FixedRng(0)) == 16  # capped
    rng = random.Random(7)
    for n in range(1, 6):
        v = backoff_minutes(n, rng)
        assert min(16, 2 ** n) <= v <= min(16, 2 ** n) * 1.25


def test_exception_mapping():
    assert error_from_exception(IngestError("EMPTY_EXTRACTION", "x")) == ("EMPTY_EXTRACTION", "x")
    assert error_from_exception(EmbeddingError("RATE_LIMITED", "slow", retryable=True))[0] == "RATE_LIMITED"
    assert error_from_exception(BlobDownloadError("down", status=503, retryable=True))[0] == "STORAGE_UNAVAILABLE"
    assert error_from_exception(BlobDownloadError("gone", status=404))[0] == "INVALID_DOCUMENT"
    assert error_from_exception(psycopg.OperationalError("x"))[0] == "DATABASE_UNAVAILABLE"
    assert error_from_exception(IndexerError("OCR_NOT_AVAILABLE", "ocr"))[0] == "OCR_NOT_AVAILABLE"
    code, msg = error_from_exception(RuntimeError("secret detail"))
    assert code == "INTERNAL" and "secret" not in msg
