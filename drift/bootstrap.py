"""Bootstrap intervals for the difference between two measured rates.

## Why bootstrap, and why it is exact here

The nonparametric bootstrap for a proportion resamples the observed 0/1 vector
with replacement. For Bernoulli data that is *identical* to drawing from
``Binomial(n, p-hat) / n`` -- resampling n values from a vector whose fraction
of ones is p-hat gives exactly that distribution. So this module samples the
binomial directly instead of shuffling lists of ones and zeros. It is not an
approximation of the bootstrap; it is the same distribution computed a faster
way, which matters because a battery produces many comparisons and each wants
thousands of resamples.

Sampling uses an inverse-CDF lookup over a probability table built with
log-gamma, so it stays accurate at the boundaries where probe results actually
live (p-hat of 0 or 1) and does not underflow.

## Seeded, always

Every interval here is reproducible from its seed, and the seed is recorded in
the comparison. An auditor who re-runs the analysis must get the same numbers;
"the confidence interval moved slightly because the random draws differed"
is not a sentence that belongs in a workpaper.
"""

from __future__ import annotations

import random
from bisect import bisect_left
from dataclasses import dataclass
from math import exp, lgamma, log
from typing import List, Sequence, Tuple

#: Enough resamples that the percentile bounds are stable to about three
#: decimal places, which is finer than any rate this toolkit reports.
DEFAULT_RESAMPLES = 10000

#: Fixed so that a comparison run today and re-run next year agree. Any value
#: would do; what matters is that it is recorded alongside the result.
DEFAULT_SEED = 20260727

__all__ = [
    "DEFAULT_RESAMPLES",
    "DEFAULT_SEED",
    "BootstrapInterval",
    "binomial_pmf",
    "binomial_cdf",
    "bootstrap_proportion_difference",
]


@dataclass(frozen=True)
class BootstrapInterval:
    """A percentile interval for a difference, with its provenance."""

    point: float
    low: float
    high: float
    confidence: float
    resamples: int
    seed: int

    @property
    def excludes_zero(self) -> bool:
        """True when the interval lies entirely on one side of zero.

        This is the significance test: if zero is a plausible value for the
        difference, the run has not demonstrated a change.
        """
        return self.low > 0.0 or self.high < 0.0

    def render(self) -> str:
        return (
            f"{self.point:+.3f} "
            f"({int(round(self.confidence * 100))}% CI "
            f"[{self.low:+.3f}, {self.high:+.3f}], "
            f"{self.resamples} resamples, seed {self.seed})"
        )


def binomial_pmf(n: int, p: float) -> List[float]:
    """Probability mass function of ``Binomial(n, p)``.

    Computed in log space so it stays accurate when p is near 0 or 1, which is
    where clean and catastrophic probe results both sit.
    """
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"p must be in [0, 1], got {p}")
    if n == 0:
        return [1.0]
    if p <= 0.0:
        return [1.0] + [0.0] * n
    if p >= 1.0:
        return [0.0] * n + [1.0]

    log_p, log_q = log(p), log(1.0 - p)
    log_n_fact = lgamma(n + 1)
    return [
        exp(
            log_n_fact
            - lgamma(k + 1)
            - lgamma(n - k + 1)
            + k * log_p
            + (n - k) * log_q
        )
        for k in range(n + 1)
    ]


def binomial_cdf(n: int, p: float) -> List[float]:
    """Cumulative distribution of ``Binomial(n, p)``, ending at exactly 1.0."""
    cdf: List[float] = []
    total = 0.0
    for mass in binomial_pmf(n, p):
        total += mass
        cdf.append(total)
    # Force the final value so a uniform draw of 0.9999... always lands.
    cdf[-1] = 1.0
    return cdf


def _sample_counts(
    cdf: Sequence[float], draws: int, rng: random.Random
) -> List[int]:
    return [bisect_left(cdf, rng.random()) for _ in range(draws)]


def bootstrap_proportion_difference(
    baseline_successes: int,
    baseline_n: int,
    current_successes: int,
    current_n: int,
    *,
    confidence: float = 0.95,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> BootstrapInterval:
    """Percentile interval for ``current_rate - baseline_rate``.

    A positive point estimate means the current rate is higher than the
    baseline. Whether higher is worse depends on the metric's direction, which
    is the caller's business, not this function's.

    Raises:
        ValueError: if either sample is empty. With no observations there is no
            difference to estimate, and returning a wide interval would invite
            the caller to treat "not measured" as "measured, inconclusive".
    """
    if baseline_n <= 0 or current_n <= 0:
        raise ValueError(
            "both samples must contain at least one trial to compare "
            f"(baseline n={baseline_n}, current n={current_n})"
        )
    for successes, n, label in (
        (baseline_successes, baseline_n, "baseline"),
        (current_successes, current_n, "current"),
    ):
        if not 0 <= successes <= n:
            raise ValueError(
                f"{label} successes {successes} outside [0, {n}]"
            )
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    if resamples < 1:
        raise ValueError(f"resamples must be positive, got {resamples}")

    baseline_p = baseline_successes / baseline_n
    current_p = current_successes / current_n

    rng = random.Random(seed)
    baseline_cdf = binomial_cdf(baseline_n, baseline_p)
    current_cdf = binomial_cdf(current_n, current_p)

    baseline_draws = _sample_counts(baseline_cdf, resamples, rng)
    current_draws = _sample_counts(current_cdf, resamples, rng)

    differences = sorted(
        current_draws[i] / current_n - baseline_draws[i] / baseline_n
        for i in range(resamples)
    )

    alpha = 1.0 - confidence
    low_index = int(alpha / 2.0 * resamples)
    high_index = min(int((1.0 - alpha / 2.0) * resamples), resamples - 1)

    return BootstrapInterval(
        point=current_p - baseline_p,
        low=differences[low_index],
        high=differences[high_index],
        confidence=confidence,
        resamples=resamples,
        seed=seed,
    )
