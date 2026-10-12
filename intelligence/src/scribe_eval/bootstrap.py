"""Paired bootstrap over shared case ids (pure python, fixed seed).

``paired_bootstrap(a, b)`` resamples case ids with replacement (10,000 times by
default) and returns the mean of ``b - a`` with a 95% percentile interval.
``not_worse(result, margin)`` is the CI gate: True only if the whole interval
lies above ``-margin`` (i.e. we are 95% sure b is not worse than a by more
than ``margin``).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Mapping

N_RESAMPLES = 10_000
SEED = 20260101


@dataclass(frozen=True)
class PairedResult:
    n: int
    mean_diff: float  # mean(b - a)
    lo: float
    hi: float

    @property
    def significant(self) -> bool:
        return self.lo > 0 or self.hi < 0


def paired_bootstrap(
    a: Mapping[str, float], b: Mapping[str, float],
    n_resamples: int = N_RESAMPLES, seed: int = SEED, alpha: float = 0.05,
) -> PairedResult:
    ids = sorted(set(a) & set(b))
    n = len(ids)
    if n == 0:
        return PairedResult(0, math.nan, math.nan, math.nan)
    diffs = [b[i] - a[i] for i in ids]
    rng = random.Random(seed)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_resamples))
    lo = means[int((alpha / 2) * n_resamples)]
    hi = means[min(n_resamples - 1, int((1 - alpha / 2) * n_resamples))]
    return PairedResult(n, sum(diffs) / n, lo, hi)


def not_worse(result: PairedResult, margin: float = 0.0) -> bool:
    """True iff the 95% interval's lower bound is >= -margin (statistically safe)."""
    return not math.isnan(result.lo) and result.lo >= -margin
