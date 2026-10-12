"""Seeded synthetic data: clustered unit vectors, Zipf text, workspace split.

Vector model (documented in performance.md): topic centres -> document centres -> chunks. Each level is
a normalised Gaussian perturbation of its parent, so chunks of one document are mutual near neighbours
(like real chunks of one file) and documents of one topic are mid-distance. Queries are perturbed
document centres. This is NOT real embedding geometry; it only mimics cluster structure."""

from __future__ import annotations

import math
import random
import uuid
from typing import Sequence

DIMS = 1536
DOC_NOISE = 0.9    # document centre = normalize(topic + DOC_NOISE * g)
CHUNK_NOISE = 0.7  # chunk = normalize(document + CHUNK_NOISE * g)
QUERY_NOISE = 0.8  # query = normalize(document + QUERY_NOISE * g)
TOPIC_QUERY_NOISE = 0.6  # topic-level query = normalize(topic + TOPIC_QUERY_NOISE * g)
DEFAULT_FRACTIONS = (0.70, 0.20, 0.07, 0.02, 0.01)
VOCAB_SIZE = 3000


def normalize(v: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def gauss_unit(rng: random.Random, dims: int = DIMS) -> list[float]:
    return normalize([rng.gauss(0.0, 1.0) for _ in range(dims)])


def perturb(base: Sequence[float], noise: float, rng: random.Random) -> list[float]:
    g = gauss_unit(rng, len(base))
    return normalize([b + noise * x for b, x in zip(base, g)])


def vec_text(v: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def split_counts(total: int, fractions: Sequence[float] = DEFAULT_FRACTIONS) -> list[int]:
    """Integer split of `total` by `fractions` (largest remainder); every workspace gets >= 1 when total allows."""
    if total <= 0:
        raise ValueError("total must be positive")
    s = sum(fractions)
    raw = [total * f / s for f in fractions]
    counts = [int(math.floor(r)) for r in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - counts[i], reverse=True)[: total - sum(counts)]:
        counts[i] += 1
    for i, c in enumerate(counts):
        if c == 0 and total >= len(counts):
            j = max(range(len(counts)), key=lambda j: counts[j])
            counts[j] -= 1
            counts[i] = 1
    return counts


def det_uuid(rng: random.Random) -> uuid.UUID:
    return uuid.UUID(int=rng.getrandbits(128), version=4)


def make_vocab(seed: int, size: int = VOCAB_SIZE) -> list[str]:
    rng = random.Random(f"vocab:{seed}")
    cons, vows = "bcdfghklmnprstvz", "aeiou"
    seen: set[str] = set()
    while len(seen) < size:
        seen.add("".join(rng.choice(cons) + rng.choice(vows) for _ in range(rng.randint(2, 4))))
    return sorted(seen)


def zipf_cum_weights(n: int, s: float = 1.1) -> list[float]:
    out, acc = [], 0.0
    for r in range(1, n + 1):
        acc += 1.0 / r**s
        out.append(acc)
    return out


def chunk_text(rng: random.Random, vocab: Sequence[str], cum: Sequence[float], words: int = 60) -> str:
    return " ".join(rng.choices(vocab, cum_weights=cum, k=words))


def lexical_queries(seed: int, vocab: Sequence[str], n: int) -> list[str]:
    """1-3 word queries drawn from the mid-frequency band (ranks 20..1500) so they have hits but are not stopword-like."""
    rng = random.Random(f"lexq:{seed}")
    lo, hi = 20, min(1500, len(vocab) - 1)
    return [" ".join(vocab[rng.randint(lo, hi)] for _ in range(1 + i % 3)) for i in range(n)]
