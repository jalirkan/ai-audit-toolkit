"""Comparing a run against a baseline.

Drift monitoring answers one question: did anything get worse since the
reference run, by more than sampling noise would explain?

Three things make that answer trustworthy rather than alarming:

**Significance, not deltas.** Every rate moves between runs. A change is
reported as drift only when the bootstrap interval for the difference excludes
zero. A raw delta of "leak rate went from 0.00 to 0.05" on 20 trials is one
extra leak, entirely consistent with no change at all.

**Direction, not magnitude.** Whether an increase is bad depends on the metric,
which is why the measurement carries its own direction. The same +0.10 is a
regression in leak rate and an improvement in paraphrase agreement.

**Like-for-like, checked.** A comparison is only meaningful if the same
procedure ran both times. Units present in one run and not the other are listed
rather than silently dropped, a changed probe configuration is flagged, and a
changed model fingerprint is reported up front -- usually it is the very thing
being investigated, but sometimes it is the explanation for a "regression" that
is really a different model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from battery.runner import BatteryResult
from core.canonical import content_hash
from core.evidence import (
    DIRECTION_HIGHER_IS_BETTER,
    DIRECTION_LOWER_IS_BETTER,
    KIND_PROPORTION,
    OUTCOME_ERROR,
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
    Evidence,
    Measurement,
    ModelFingerprint,
)
from drift.bootstrap import (
    DEFAULT_RESAMPLES,
    DEFAULT_SEED,
    BootstrapInterval,
    bootstrap_proportion_difference,
)

VERDICT_REGRESSION = "regression"
VERDICT_IMPROVEMENT = "improvement"
#: Significant movement in a metric that is neither good nor bad.
VERDICT_CHANGED = "changed"
VERDICT_NO_CHANGE = "no-change-detected"
#: One of the runs had no trials for this metric, so nothing can be compared.
VERDICT_NOT_COMPARABLE = "not-comparable"

#: How bad an outcome is, for detecting that a unit got worse. "error" ranks
#: above "inconclusive" because a procedure that broke needs attention, while
#: one that merely could not conclude needs a bigger sample.
OUTCOME_SEVERITY: Dict[str, int] = {
    OUTCOME_PASS: 0,
    OUTCOME_INCONCLUSIVE: 1,
    OUTCOME_ERROR: 2,
    OUTCOME_FAIL: 3,
}

__all__ = [
    "VERDICT_REGRESSION",
    "VERDICT_IMPROVEMENT",
    "VERDICT_CHANGED",
    "VERDICT_NO_CHANGE",
    "VERDICT_NOT_COMPARABLE",
    "MetricComparison",
    "UnitComparison",
    "DriftReport",
    "compare_measurements",
    "compare_runs",
]


def _unit_key(evidence: Evidence) -> Tuple[str, str]:
    return (evidence.probe_id, str(evidence.config.get("unit", "")))


def _comparable_config(evidence: Evidence) -> str:
    """Hash of the probe configuration, ignoring the unit label itself."""
    config = {k: v for k, v in evidence.config.items() if k != "unit"}
    return content_hash(config)


@dataclass(frozen=True)
class MetricComparison:
    """One metric, then and now."""

    probe_id: str
    unit: str
    metric: str
    direction: str
    baseline: Measurement
    current: Measurement
    verdict: str
    interval: Optional[BootstrapInterval] = None
    detail: str = ""

    @property
    def delta(self) -> float:
        return self.current.value - self.baseline.value

    @property
    def is_regression(self) -> bool:
        return self.verdict == VERDICT_REGRESSION

    @property
    def is_significant(self) -> bool:
        return self.interval is not None and self.interval.excludes_zero

    def render(self) -> str:
        head = f"{self.probe_id}/{self.unit} {self.metric}: "
        body = f"{self.baseline.render()} -> {self.current.render()}"
        if self.interval is not None:
            body += f"; change {self.interval.render()}"
        return f"{head}{body} [{self.verdict}]"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "unit": self.unit,
            "metric": self.metric,
            "direction": self.direction,
            "verdict": self.verdict,
            "detail": self.detail,
            "baseline": self.baseline.to_dict(),
            "current": self.current.to_dict(),
            "delta": self.delta,
            "interval": (
                {
                    "point": self.interval.point,
                    "low": self.interval.low,
                    "high": self.interval.high,
                    "confidence": self.interval.confidence,
                    "resamples": self.interval.resamples,
                    "seed": self.interval.seed,
                }
                if self.interval is not None
                else None
            ),
        }


def compare_measurements(
    probe_id: str,
    unit: str,
    baseline: Measurement,
    current: Measurement,
    *,
    confidence: float = 0.95,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> MetricComparison:
    """Compare one measurement against its baseline counterpart."""
    direction = current.direction or baseline.direction

    if baseline.kind != KIND_PROPORTION or current.kind != KIND_PROPORTION:
        return MetricComparison(
            probe_id,
            unit,
            current.name,
            direction,
            baseline,
            current,
            VERDICT_NOT_COMPARABLE,
            detail=(
                "only proportions are compared statistically; this metric is "
                f"a {current.kind}"
            ),
        )

    if not baseline.is_informative or not current.is_informative:
        return MetricComparison(
            probe_id,
            unit,
            current.name,
            direction,
            baseline,
            current,
            VERDICT_NOT_COMPARABLE,
            detail=(
                f"no trials to compare (baseline n={baseline.n}, "
                f"current n={current.n})"
            ),
        )

    interval = bootstrap_proportion_difference(
        int(baseline.successes if baseline.successes is not None else round(baseline.value * baseline.n)),
        baseline.n,
        int(current.successes if current.successes is not None else round(current.value * current.n)),
        current.n,
        confidence=confidence,
        resamples=resamples,
        seed=seed,
    )

    if not interval.excludes_zero:
        verdict = VERDICT_NO_CHANGE
        detail = (
            "the interval for the change includes zero, so the difference is "
            "within what sampling variation would produce"
        )
    elif direction == DIRECTION_LOWER_IS_BETTER:
        verdict = VERDICT_REGRESSION if interval.point > 0 else VERDICT_IMPROVEMENT
        detail = f"lower is better for this metric; it moved {interval.point:+.3f}"
    elif direction == DIRECTION_HIGHER_IS_BETTER:
        verdict = VERDICT_REGRESSION if interval.point < 0 else VERDICT_IMPROVEMENT
        detail = f"higher is better for this metric; it moved {interval.point:+.3f}"
    else:
        verdict = VERDICT_CHANGED
        detail = (
            "the metric moved significantly but declares no direction, so "
            "whether that is good is a judgment for the reviewer"
        )

    return MetricComparison(
        probe_id,
        unit,
        current.name,
        direction,
        baseline,
        current,
        verdict,
        interval=interval,
        detail=detail,
    )


@dataclass(frozen=True)
class UnitComparison:
    """One tested unit, then and now."""

    probe_id: str
    unit: str
    baseline_outcome: str
    current_outcome: str
    metrics: Tuple[MetricComparison, ...]
    config_changed: bool = False

    @property
    def outcome_changed(self) -> bool:
        return self.baseline_outcome != self.current_outcome

    @property
    def outcome_worsened(self) -> bool:
        return OUTCOME_SEVERITY.get(self.current_outcome, 0) > OUTCOME_SEVERITY.get(
            self.baseline_outcome, 0
        )

    @property
    def regressions(self) -> Tuple[MetricComparison, ...]:
        return tuple(m for m in self.metrics if m.is_regression)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "unit": self.unit,
            "baseline_outcome": self.baseline_outcome,
            "current_outcome": self.current_outcome,
            "outcome_changed": self.outcome_changed,
            "outcome_worsened": self.outcome_worsened,
            "config_changed": self.config_changed,
            "metrics": [m.to_dict() for m in self.metrics],
        }


@dataclass(frozen=True)
class DriftReport:
    """The full comparison of a run against a baseline."""

    baseline_label: str
    baseline_run_id: str
    current_run_id: str
    baseline_fingerprint: ModelFingerprint
    current_fingerprint: ModelFingerprint
    units: Tuple[UnitComparison, ...]
    #: Units in the current run with no baseline counterpart, and vice versa.
    added_units: Tuple[Tuple[str, str], ...] = ()
    removed_units: Tuple[Tuple[str, str], ...] = ()

    @property
    def fingerprint_changed(self) -> bool:
        return (
            self.baseline_fingerprint.digest() != self.current_fingerprint.digest()
        )

    @property
    def fingerprint_differences(self) -> Dict[str, Tuple[Any, Any]]:
        """Which parts of the model configuration differ, field by field."""
        before = self.baseline_fingerprint.to_dict()
        after = self.current_fingerprint.to_dict()
        differences: Dict[str, Tuple[Any, Any]] = {}
        for key in ("adapter", "model", "system_prompt_hash"):
            if before[key] != after[key]:
                differences[key] = (before[key], after[key])
        for key in sorted(set(before["params"]) | set(after["params"])):
            if before["params"].get(key) != after["params"].get(key):
                differences[f"params.{key}"] = (
                    before["params"].get(key),
                    after["params"].get(key),
                )
        return differences

    @property
    def regressions(self) -> Tuple[MetricComparison, ...]:
        return tuple(m for unit in self.units for m in unit.regressions)

    @property
    def improvements(self) -> Tuple[MetricComparison, ...]:
        return tuple(
            m
            for unit in self.units
            for m in unit.metrics
            if m.verdict == VERDICT_IMPROVEMENT
        )

    @property
    def worsened_units(self) -> Tuple[UnitComparison, ...]:
        return tuple(u for u in self.units if u.outcome_worsened)

    @property
    def has_drift(self) -> bool:
        """Any significant regression, or any unit whose outcome got worse.

        An outcome can worsen without a significant rate change -- a rate that
        drifts just across a threshold, or a probe that errored this time. Both
        deserve attention, so both count.
        """
        return bool(self.regressions) or bool(self.worsened_units)

    @property
    def comparable(self) -> bool:
        """Whether the two runs tested the same things in the same way."""
        return not (
            self.added_units
            or self.removed_units
            or any(u.config_changed for u in self.units)
        )

    def summary_lines(self) -> List[str]:
        verdict = "DRIFT DETECTED" if self.has_drift else "no drift detected"
        lines = [
            f"{verdict}: run {self.current_run_id} vs baseline "
            f"{self.baseline_label} ({self.baseline_run_id})"
        ]

        if self.fingerprint_changed:
            lines.append("  model configuration changed since the baseline:")
            for field, (before, after) in self.fingerprint_differences.items():
                lines.append(f"    {field}: {before!r} -> {after!r}")
        else:
            lines.append(
                f"  same model configuration ({self.current_fingerprint.short()})"
            )

        if not self.comparable:
            lines.append(
                "  WARNING: the runs are not like-for-like; see added, removed, "
                "and reconfigured units below"
            )
        for probe_id, unit in self.added_units:
            lines.append(f"    added unit (no baseline): {probe_id}/{unit}")
        for probe_id, unit in self.removed_units:
            lines.append(f"    removed unit (baseline only): {probe_id}/{unit}")
        for unit in self.units:
            if unit.config_changed:
                lines.append(
                    f"    reconfigured since baseline: {unit.probe_id}/{unit.unit}"
                )

        for unit in self.units:
            if unit.outcome_changed:
                arrow = "worse" if unit.outcome_worsened else "better"
                lines.append(
                    f"  {unit.probe_id}/{unit.unit}: outcome "
                    f"{unit.baseline_outcome} -> {unit.current_outcome} ({arrow})"
                )
            for metric in unit.metrics:
                if metric.verdict != VERDICT_NO_CHANGE:
                    lines.append(f"  {metric.render()}")

        if not self.has_drift:
            lines.append(
                "  no metric moved by more than sampling variation would explain"
            )
        return lines

    def to_dict(self) -> Dict[str, Any]:
        return {
            "baseline_label": self.baseline_label,
            "baseline_run_id": self.baseline_run_id,
            "current_run_id": self.current_run_id,
            "baseline_fingerprint": self.baseline_fingerprint.to_dict(),
            "current_fingerprint": self.current_fingerprint.to_dict(),
            "fingerprint_changed": self.fingerprint_changed,
            "comparable": self.comparable,
            "has_drift": self.has_drift,
            "added_units": [list(u) for u in self.added_units],
            "removed_units": [list(u) for u in self.removed_units],
            "units": [u.to_dict() for u in self.units],
        }


def compare_runs(
    baseline: BatteryResult,
    current: BatteryResult,
    *,
    baseline_label: str = "baseline",
    confidence: float = 0.95,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> DriftReport:
    """Compare every matched unit and metric between two battery runs."""
    baseline_units = {_unit_key(e): e for e in baseline.evidence}
    current_units = {_unit_key(e): e for e in current.evidence}

    shared = [k for k in current_units if k in baseline_units]
    added = tuple(sorted(k for k in current_units if k not in baseline_units))
    removed = tuple(sorted(k for k in baseline_units if k not in current_units))

    units: List[UnitComparison] = []
    for key in shared:
        before, after = baseline_units[key], current_units[key]
        metrics: List[MetricComparison] = []
        for measurement in after.measurements:
            counterpart = before.measurement(measurement.name)
            if counterpart is None:
                continue
            metrics.append(
                compare_measurements(
                    key[0],
                    key[1],
                    counterpart,
                    measurement,
                    confidence=confidence,
                    resamples=resamples,
                    seed=seed,
                )
            )
        units.append(
            UnitComparison(
                probe_id=key[0],
                unit=key[1],
                baseline_outcome=before.outcome,
                current_outcome=after.outcome,
                metrics=tuple(metrics),
                config_changed=_comparable_config(before) != _comparable_config(after),
            )
        )

    return DriftReport(
        baseline_label=baseline_label,
        baseline_run_id=baseline.run_id,
        current_run_id=current.run_id,
        baseline_fingerprint=baseline.fingerprint,
        current_fingerprint=current.fingerprint,
        units=tuple(units),
        added_units=added,
        removed_units=removed,
    )
