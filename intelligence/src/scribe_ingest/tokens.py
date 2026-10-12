"""Token counting (DESIGN D8).

`count_tokens` uses tiktoken's `o200k_base` BPE as a *recorded proxy* for the
embedding model's tokenizer (there is no local Gemini tokenizer). The id of the
proxy is stored in the chunk profile as `TOKENIZER_ID`.

The BPE vocabulary is vendored under `data/tiktoken_cache/` so that nothing is
downloaded at runtime (Vercel cold starts, offline CI). tiktoken looks the file
up in `TIKTOKEN_CACHE_DIR` under the sha1 of the blob URL; the vendored file is
stored under exactly that name (sha1("https://openaipublic.blob.core.windows.net/
encodings/o200k_base.tiktoken") == fb374d41...). The cache env var is pointed at
the vendored dir the first time this module is imported, only if the user has not
set `TIKTOKEN_CACHE_DIR` (or `DATA_GYM_CACHE_DIR`) themselves; a user-supplied
cache dir must already contain the vocabulary or tiktoken will try the network.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

TOKENIZER_ID = "tiktoken:o200k_base"
_ENCODING_NAME = "o200k_base"
VENDORED_CACHE_DIR = Path(__file__).resolve().parent / "data" / "tiktoken_cache"

if not os.environ.get("TIKTOKEN_CACHE_DIR") and not os.environ.get("DATA_GYM_CACHE_DIR"):
    os.environ["TIKTOKEN_CACHE_DIR"] = str(VENDORED_CACHE_DIR)


@lru_cache(maxsize=1)
def _encoding():
    import tiktoken

    return tiktoken.get_encoding(_ENCODING_NAME)


def count_tokens(text: str) -> int:
    """Number of o200k_base tokens in `text` (special-token strings are plain text)."""
    if not text:
        return 0
    return len(_encoding().encode(text, disallowed_special=()))
