"""Running one battery against several endpoints and laying the results side by side.

The question this answers is the procurement one: *given these candidate
endpoints, which should we trust with this workload?* It is the same evidence
the single-endpoint path produces, arranged so the differences are visible.

## No overall ranking

There is no "winner" column, and no aggregate score per endpoint, for the same
reason a battery has no composite score (D-016): a leak rate and an agreement
rate are not commensurable, and a mean of them is not a quantity. An endpoint
that never leaks but contradicts itself under paraphrase is better or worse
than the reverse depending entirely on what it is being used for, which is a
judgment for the reader and not one this module is entitled to make.

What it does instead is put the outcomes and the intervals next to each other
and mark where the intervals overlap -- because two endpoints whose intervals
overlap have not been shown to differ, and presenting them as ranked would
invent a distinction the sample does not support.

## Operational figures, not costs

Call counts, mean latency (with a bootstrap interval, since latency
distributions are skewed and a normal approximation would misstate them), and
token totals the adapters actually reported are shown. Prices are not,
deliberately: they change, they vary by contract, and a stale price baked into
an audit artifact is worse than no price at all. Multiply by your own rate
card.

Token totals are sums of what landed on each ``Trial``. When an adapter did not
report usage for a call, that absence is counted rather than filled in -- a
total over a partial set of calls says how many calls contributed, never that
the missing ones used zero tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from adapters.base import ModelAdapter
from battery.runner import BatteryResult, run_battery
from battery.spec import BatterySpec
from core.evidence import (
    CI_BOOTSTRAP,
    KIND_MEAN,
    OUTCOME_ERROR,
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
    Evidence,
    Measurement,
)
from drift.bootstrap import DEFAULT_SEED, bootstrap_mean_interval
from journal.store import Journal

__all__ = [
    "EndpointRun",
    "TokenAccounting",
    "MetricRow",
    "ComparisonMatrix",
    "run_comparison",
]


def _unit_key(evidence: Evidence) -> Tuple[str, str]:
    return (evidence.probe_id, str(evidence.config.get("unit", "")))


@dataclass(frozen=True)
class TokenAccounting:
    """Token totals drawn only from trials that reported usage.

    ``prompt_tokens`` / ``completion_tokens`` are None when no trial reported
    that field -- not zero. ``calls_without_usage`` makes the coverage gap
    visible so a partial total cannot be mistaken for a complete one.
    """

    calls: int
    calls_with_usage: int
    calls_without_usage: int
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]

    @property
    def total_tokens(self) -> Optional[int]:
        if self.prompt_tokens is None and self.completion_tokens is None:
            return None
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)

    def coverage_label(self) -> str:
        return f"{self.calls_with_usage}/{self.calls} calls reported usage"

    def render_prompt(self) -> str:
        if self.prompt_tokens is None:
            return "not reported"
        if self.calls_without_usage:
            return f"{self.prompt_tokens} ({self.coverage_label()})"
        return str(self.prompt_tokens)

    def render_completion(self) -> str:
        if self.completion_tokens is None:
            return "not reported"
        if self.calls_without_usage:
            return f"{self.completion_tokens} ({self.coverage_label()})"
        return str(self.completion_tokens)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "calls": self.calls,
            "calls_with_usage": self.calls_with_usage,
            "calls_without_usage": self.calls_without_usage,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class EndpointRun:
    """One endpoint's result, with the label the operator gave it."""

    label: str
    description: str
    result: BatteryResult

    @property
    def latencies(self) -> List[float]:
        return [
            trial.latency_ms
            for evidence in self.result.evidence
            for trial in evidence.trials
        ]

    def latency_measurement(
        self, *, seed: int = DEFAULT_SEED, resamples: int = 2000
    ) -> Optional[Measurement]:
        """Mean response latency with a bootstrap interval.

        Returns None when there is nothing to measure, rather than a zero.
        """
        values = self.latencies
        if not values:
            return None
        interval = bootstrap_mean_interval(
            values, resamples=resamples, seed=seed
        )
        if interval.low == float("-inf"):
            return None
        return Measurement(
            name="latency_ms",
            kind=KIND_MEAN,
            value=interval.point,
            n=len(values),
            ci_low=interval.low,
            ci_high=interval.high,
            ci_method=CI_BOOTSTRAP,
            confidence=interval.confidence,
            method_note=(
                "Mean latency across every model call in the run, with a "
                "nonparametric bootstrap interval."
            ),
        )

    @property
    def total_calls(self) -> int:
        return self.result.total_trials

    def token_accounting(self) -> TokenAccounting:
        """Sum adapter-reported token counts; never estimate the missing ones."""
        calls = 0
        with_usage = 0
        prompt_sum = 0
        completion_sum = 0
        saw_prompt = False
        saw_completion = False
        for evidence in self.result.evidence:
            for trial in evidence.trials:
                calls += 1
                if trial.usage is None:
                    continue
                with_usage += 1
                if "prompt_tokens" in trial.usage:
                    prompt_sum += trial.usage["prompt_tokens"]
                    saw_prompt = True
                if "completion_tokens" in trial.usage:
                    completion_sum += trial.usage["completion_tokens"]
                    saw_completion = True
        return TokenAccounting(
            calls=calls,
            calls_with_usage=with_usage,
            calls_without_usage=calls - with_usage,
            prompt_tokens=prompt_sum if saw_prompt else None,
            completion_tokens=completion_sum if saw_completion else None,
        )


@dataclass(frozen=True)
class MetricRow:
    """One measurement, across every endpoint that reported it."""

    probe_id: str
    unit: str
    metric: str
    direction: str
    by_label: Dict[str, Measurement]

    def rendered(self, label: str) -> str:
        measurement = self.by_label.get(label)
        return measurement.render() if measurement else "not tested"

    def overlapping_labels(self) -> List[str]:
        """Labels whose interval overlaps every other reported interval.

        When every interval overlaps, the run has not distinguished the
        endpoints on this metric at all, and the matrix says so instead of
        letting the reader order them by point estimate.
        """
        usable = {
            label: m
            for label, m in self.by_label.items()
            if m.ci_low is not None and m.ci_high is not None and m.is_informative
        }
        if len(usable) < 2:
            return []
        labels = sorted(usable)
        overlapping = []
        for label in labels:
            mine = usable[label]
            if all(
                mine.ci_low <= usable[other].ci_high
                and usable[other].ci_low <= mine.ci_high
                for other in labels
                if other != label
            ):
                overlapping.append(label)
        return overlapping

    @property
    def all_overlap(self) -> bool:
        return len(self.overlapping_labels()) == len(
            [m for m in self.by_label.values() if m.is_informative]
        ) and len(self.by_label) > 1

    def to_dict(self) -> Dict[str, Any]:
        """This row with every endpoint's measurement kept whole.

        The measurements travel because a consumer that only learns *which*
        metrics failed to separate the endpoints cannot show the reader why.
        Drawing the overlapping intervals on one scale is the honest way to
        present "not distinguished", and that needs the bounds, not a name.

        ``all_overlap`` is computed here rather than left to the consumer so
        the rule in :meth:`overlapping_labels` has exactly one home. A second
        implementation deciding what a run did or did not establish is the
        failure this project exists to prevent.
        """
        return {
            "probe_id": self.probe_id,
            "unit": self.unit,
            "metric": self.metric,
            "direction": self.direction,
            "all_overlap": self.all_overlap,
            "overlapping_labels": self.overlapping_labels(),
            "by_label": {
                label: measurement.to_dict()
                for label, measurement in self.by_label.items()
            },
        }


@dataclass(frozen=True)
class ComparisonMatrix:
    """One battery, several endpoints, arranged for comparison."""

    battery: str
    endpoints: Tuple[EndpointRun, ...]

    @property
    def labels(self) -> List[str]:
        return [e.label for e in self.endpoints]

    @property
    def units(self) -> List[Tuple[str, str]]:
        """Every (probe, unit) tested, in first-appearance order."""
        seen: List[Tuple[str, str]] = []
        for endpoint in self.endpoints:
            for evidence in endpoint.result.evidence:
                key = _unit_key(evidence)
                if key not in seen:
                    seen.append(key)
        return seen

    def outcome(self, label: str, unit: Tuple[str, str]) -> str:
        endpoint = next((e for e in self.endpoints if e.label == label), None)
        if endpoint is None:
            return "-"
        for evidence in endpoint.result.evidence:
            if _unit_key(evidence) == unit:
                return evidence.outcome
        return "not tested"

    def metric_rows(self) -> List[MetricRow]:
        rows: Dict[Tuple[str, str, str], Dict[str, Measurement]] = {}
        directions: Dict[Tuple[str, str, str], str] = {}
        order: List[Tuple[str, str, str]] = []
        for endpoint in self.endpoints:
            for evidence in endpoint.result.evidence:
                probe_id, unit = _unit_key(evidence)
                for measurement in evidence.measurements:
                    key = (probe_id, unit, measurement.name)
                    if key not in rows:
                        rows[key] = {}
                        directions[key] = measurement.direction
                        order.append(key)
                    rows[key][endpoint.label] = measurement
        return [
            MetricRow(
                probe_id=key[0],
                unit=key[1],
                metric=key[2],
                direction=directions[key],
                by_label=rows[key],
            )
            for key in order
        ]

    def undistinguished_metrics(self) -> List[MetricRow]:
        """Metrics where every endpoint's interval overlaps every other."""
        return [row for row in self.metric_rows() if row.all_overlap]

    def summary_lines(self) -> List[str]:
        lines = [
            f"{self.battery}: {len(self.endpoints)} endpoint(s) compared",
            "",
        ]
        width = max((len(l) for l in self.labels), default=8)
        for endpoint in self.endpoints:
            counts = endpoint.result.outcome_counts
            tally = "  ".join(f"{k}={v}" for k, v in sorted(counts.items()) if v)
            lines.append(
                f"  {endpoint.label:<{width}}  [{endpoint.result.outcome.upper()}]  "
                f"{tally}  ({endpoint.description})"
            )
        lines.append("")
        for probe_id, unit in self.units:
            lines.append(f"  {probe_id} / {unit or '-'}")
            for label in self.labels:
                lines.append(
                    f"    {label:<{width}}  {self.outcome(label, (probe_id, unit))}"
                )
        undistinguished = self.undistinguished_metrics()
        if undistinguished:
            lines.append("")
            lines.append(
                "  Not distinguished by this run -- every interval overlaps, so "
                "the endpoints have not been shown to differ:"
            )
            for row in undistinguished:
                lines.append(f"    {row.probe_id}/{row.unit} {row.metric}")
        lines.append("")
        lines.append(
            "  No overall ranking is produced. The metrics are not "
            "commensurable, and which matters depends on the workload."
        )
        return lines

    def to_dict(self) -> Dict[str, Any]:
        return {
            "battery": self.battery,
            "endpoints": [
                {
                    "label": e.label,
                    "description": e.description,
                    "outcome": e.result.outcome,
                    "outcome_counts": e.result.outcome_counts,
                    "run_id": e.result.run_id,
                    "fingerprint": e.result.fingerprint.to_dict(),
                    "total_calls": e.result.total_trials,
                    "tokens": e.token_accounting().to_dict(),
                }
                for e in self.endpoints
            ],
            "units": [
                {
                    "probe_id": probe_id,
                    "unit": unit,
                    "outcomes": {
                        label: self.outcome(label, (probe_id, unit))
                        for label in self.labels
                    },
                }
                for probe_id, unit in self.units
            ],
            # Every metric with its per-endpoint measurements intact. Additive:
            # `undistinguished_metrics` below keeps its original shape, so a
            # consumer written against the earlier payload is unaffected.
            "metric_rows": [r.to_dict() for r in self.metric_rows()],
            "undistinguished_metrics": [
                {"probe_id": r.probe_id, "unit": r.unit, "metric": r.metric}
                for r in self.undistinguished_metrics()
            ],
        }


def run_comparison(
    spec: BatterySpec,
    endpoints: Sequence[Tuple[str, ModelAdapter]],
    *,
    journal: Optional[Journal] = None,
) -> ComparisonMatrix:
    """Run ``spec`` against each labelled adapter and build the matrix.

    Every endpoint gets the identical battery. Comparing runs of different
    suites would produce a table that looks like a comparison and is not one.
    """
    if not endpoints:
        raise ValueError("a comparison needs at least one endpoint")
    labels = [label for label, _ in endpoints]
    if len(set(labels)) != len(labels):
        raise ValueError(f"endpoint labels must be unique, got {labels}")

    runs: List[EndpointRun] = []
    for label, adapter in endpoints:
        result = run_battery(spec, adapter, journal=journal)
        runs.append(
            EndpointRun(
                label=label, description=adapter.describe(), result=result
            )
        )
    return ComparisonMatrix(battery=spec.name, endpoints=tuple(runs))
