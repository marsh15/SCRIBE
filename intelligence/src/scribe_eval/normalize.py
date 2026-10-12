"""The single text normalization used for evidence matching (DESIGN D17).

A retrieved chunk is relevant if normalize(chunk) contains normalize(quote).
Retrieval metrics must import this function rather than re-implementing it.

Steps: Unicode NFKC, curly quotes/dashes to ASCII, lowercase, collapse every
whitespace run to one space, strip.
"""

from __future__ import annotations

import re
import unicodedata

_TRANSLATE = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
        "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
        "―": "-", "−": "-",
        " ": " ", "​": "", "﻿": "",
    }
)
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE)
    return _WS.sub(" ", text.lower()).strip()
