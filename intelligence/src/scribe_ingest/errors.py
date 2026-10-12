"""Ingest errors. Messages are plain language and safe to show to users."""

from __future__ import annotations

from typing import Literal

IngestErrorCode = Literal[
    "INVALID_DOCUMENT",
    "UNSUPPORTED_FORMAT",
    "EMPTY_EXTRACTION",
    "RESOURCE_LIMIT",
]


class IngestError(Exception):
    """A permanent, user-explainable failure. Retrying the same bytes will not help."""

    def __init__(self, code: IngestErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: IngestErrorCode = code
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code}: {self.message}"
