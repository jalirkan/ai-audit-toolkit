"""Statistical primitives for reported scores.

Standard library only (``statistics.NormalDist`` supplies the normal quantile).

Every rate this toolkit reports is an estimate computed from a finite sample of
model calls. A rate printed without its sample size and interval invites the
reader to treat 3/4 and 300/400 as the same fact, which they are not. So the
interval is computed here and carried, by construction, everywhere the rate
goes (see DECISIONS D-004).

Wilson score interval is the default for proportions rather than the textbook
normal approximation, because probe results routinely land at or near 0 and 1
(no injections leaked, every paraphrase agreed). The normal approximation
degenerates there -- it produces zero-width intervals for 0/n and can run past
[0, 1] -- while Wilson stays sensible and asymmetric at the boundaries.
"""

from __future__ import annotations

from statistics import NormalDist
from typing import Sequence, Tuple

DEFAULT_CONFIDENCE = 0.95

__all__ = [
    "DEFAULT_CONFIDENCE",
    "z_for_confidence",
    "wilson_interval",
    "clamp_unit",
    "mean",
]


def z_for_confidence(confidence: float = DEFAULT_CONFIDENCE) -> float:
    """Two-sided standard-normal critical value for ``confidence``.

    ``z_for_confidence(0.95)`` is the familiar 1.959963...
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence!r}")
    alpha = 1.0 - confidence
    return NormalDist().inv_cdf(1.0 - alpha / 2.0)


def clamp_unit(x: float) -> float:
    """Clamp a float into [0.0, 1.0], absorbing floating-point overshoot."""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def wilson_interval(
    successes: int,
    n: int,
    confidence: float = DEFAULT_CONFIDENCE,
) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Args:
        successes: count of the outcome being measured, 0 <= successes <= n.
        n: sample size (number of trials actually performed).
        confidence: coverage level, e.g. 0.95.

    Returns:
        ``(low, high)``, both clamped to [0, 1].

    With ``n == 0`` there is no evidence at all, so the honest interval is the
    whole unit line, ``(0.0, 1.0)``. Callers should treat that as "not tested"
    rather than as a measurement.
    """
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n!r}")
    if successes < 0:
        raise ValueError(f"successes must be non-negative, got {successes!r}")
    if successes > n:
        raise ValueError(f"successes ({successes}) cannot exceed n ({n})")
    if n == 0:
        return (0.0, 1.0)

    z = z_for_confidence(confidence)
    z2 = z * z
    p = successes / n

    denominator = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denominator
    margin = (z / denominator) * ((p * (1.0 - p) / n + z2 / (4.0 * n * n)) ** 0.5)

    low = clamp_unit(center - margin)
    high = clamp_unit(center + margin)

    # At the boundaries the Wilson bounds are analytically exact -- zero
    # successes gives a lower bound of exactly 0, and n successes an upper
    # bound of exactly 1 -- but the arithmetic above leaves residue on the
    # order of 1e-17. Snap them, so a workpaper reports "0.000" rather than a
    # number that invites a reader to wonder what it means.
    if successes == 0:
        low = 0.0
    if successes == n:
        high = 1.0

    return (low, high)


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean; 0.0 for an empty sequence.

    Present so callers do not scatter their own empty-sequence handling around
    and accidentally raise on a zero-trial probe.
    """
    if not values:
        return 0.0
    return sum(values) / len(values)
