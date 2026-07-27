"""The evidence record -- the unit of everything this toolkit produces.

Per DECISIONS D-002, probes do not return verdicts, they return evidence: what
was sent, what came back, what was measured, with how much uncertainty, against
which model configuration, at what time. Conclusions are rendered later, from
stored evidence, and can always be traced back to the trials that produced
them.

Three constraints are enforced in code here rather than left to reviewer
discipline, because a convention that is merely documented is a convention that
eventually gets skipped:

1. A ``Measurement`` of kind ``proportion`` or ``mean`` cannot be constructed
   without an interval and a sample size (D-004). There is no code path that
   yields a bare rate.
2. Anything placed in an evidence record must survive canonical JSON encoding,
   checked at construction time, so a record cannot be built now and fail to
   store or re-verify later.
3. Every record carries ``schema_version``, so a journal written today stays
   interpretable after the shape of these objects changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from core.canonical import content_hash, is_json_serializable
from core.stats import DEFAULT_CONFIDENCE, wilson_interval

SCHEMA_VERSION = 1

# --- measurement kinds -------------------------------------------------------
KIND_PROPORTION = "proportion"
KIND_MEAN = "mean"
KIND_COUNT = "count"
MEASUREMENT_KINDS = frozenset({KIND_PROPORTION, KIND_MEAN, KIND_COUNT})

#: Kinds that are estimates of an unknown quantity and therefore may not be
#: reported without an interval.
KINDS_REQUIRING_INTERVAL = frozenset({KIND_PROPORTION, KIND_MEAN})

# --- metric direction --------------------------------------------------------
#: Which way is good. Recorded on the measurement rather than known only to the
#: probe that produced it, because every downstream consumer needs it: drift
#: cannot tell a regression from an improvement without it, and a report cannot
#: phrase a finding without it.
DIRECTION_LOWER_IS_BETTER = "lower_is_better"
DIRECTION_HIGHER_IS_BETTER = "higher_is_better"
#: For quantities that are neither good nor bad, such as a raw tally.
DIRECTION_NEUTRAL = "neutral"
DIRECTIONS = frozenset(
    {DIRECTION_LOWER_IS_BETTER, DIRECTION_HIGHER_IS_BETTER, DIRECTION_NEUTRAL}
)

# --- interval methods --------------------------------------------------------
CI_WILSON = "wilson"
CI_BOOTSTRAP = "bootstrap-percentile"
CI_NONE = "none"

# --- outcomes ----------------------------------------------------------------
OUTCOME_PASS = "pass"
OUTCOME_FAIL = "fail"
#: Ran, but the sample does not support a conclusion either way (e.g. n too
#: small, or an interval that straddles the threshold). Distinct from ERROR:
#: nothing went wrong, the evidence is simply not sufficient.
OUTCOME_INCONCLUSIVE = "inconclusive"
#: The procedure itself failed -- adapter raised, endpoint unreachable. Not a
#: finding about the model.
OUTCOME_ERROR = "error"
OUTCOMES = frozenset(
    {OUTCOME_PASS, OUTCOME_FAIL, OUTCOME_INCONCLUSIVE, OUTCOME_ERROR}
)

_CONTAINMENT_TOLERANCE = 1e-9

__all__ = [
    "SCHEMA_VERSION",
    "KIND_PROPORTION",
    "KIND_MEAN",
    "KIND_COUNT",
    "MEASUREMENT_KINDS",
    "DIRECTION_LOWER_IS_BETTER",
    "DIRECTION_HIGHER_IS_BETTER",
    "DIRECTION_NEUTRAL",
    "DIRECTIONS",
    "CI_WILSON",
    "CI_BOOTSTRAP",
    "CI_NONE",
    "OUTCOME_PASS",
    "OUTCOME_FAIL",
    "OUTCOME_INCONCLUSIVE",
    "OUTCOME_ERROR",
    "OUTCOMES",
    "utc_now_iso",
    "Measurement",
    "ModelFingerprint",
    "Trial",
    "Evidence",
]


def utc_now_iso() -> str:
    """Current UTC time as ``YYYY-MM-DDTHH:MM:SS.ffffffZ``.

    Always UTC and always suffixed ``Z``: evidence gets compared across
    machines, and a local-time timestamp in an audit trail is a defect.
    """
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _require_json(value: Any, label: str) -> None:
    if not is_json_serializable(value):
        raise TypeError(
            f"{label} must be JSON-serializable to be stored as evidence; "
            f"got {type(value).__name__}"
        )


@dataclass(frozen=True)
class Measurement:
    """A single measured quantity, inseparable from its uncertainty.

    Construct proportions via :meth:`proportion` rather than by hand -- it
    computes the interval for you and keeps the numerator, which is what an
    auditor actually wants to see ("3 of 25", not "0.12").
    """

    name: str
    kind: str
    value: float
    n: int
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    ci_method: str = CI_NONE
    confidence: Optional[float] = None
    #: Numerator for proportions -- retained so reports can show counts.
    successes: Optional[int] = None
    #: Free-text note on how the number was arrived at (the scoring rule).
    method_note: str = ""
    #: Which way is good. See the DIRECTION_* constants.
    direction: str = DIRECTION_NEUTRAL

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("measurement name must be non-empty")
        if self.kind not in MEASUREMENT_KINDS:
            raise ValueError(
                f"unknown measurement kind {self.kind!r}; "
                f"expected one of {sorted(MEASUREMENT_KINDS)}"
            )
        if self.direction not in DIRECTIONS:
            raise ValueError(
                f"unknown direction {self.direction!r}; "
                f"expected one of {sorted(DIRECTIONS)}"
            )
        if self.n < 0:
            raise ValueError(f"n must be non-negative, got {self.n!r}")

        has_interval = self.ci_low is not None and self.ci_high is not None

        if self.kind in KINDS_REQUIRING_INTERVAL:
            # D-004: this is the structural guarantee, not a style preference.
            if not has_interval:
                raise ValueError(
                    f"measurement {self.name!r} of kind {self.kind!r} requires a "
                    "confidence interval; a bare rate is not reportable"
                )
            if self.ci_method == CI_NONE:
                raise ValueError(
                    f"measurement {self.name!r} must name the method used to "
                    "compute its interval"
                )
            if self.confidence is None:
                raise ValueError(
                    f"measurement {self.name!r} must state its confidence level"
                )

        if has_interval:
            if self.ci_low > self.ci_high:
                raise ValueError(
                    f"measurement {self.name!r} has ci_low {self.ci_low} above "
                    f"ci_high {self.ci_high}"
                )
            # Wilson always brackets the point estimate, so a violation means a
            # construction bug. Bootstrap percentile intervals are not checked:
            # they legitimately can exclude the point estimate on skewed
            # resampling distributions.
            if self.ci_method == CI_WILSON and not (
                self.ci_low - _CONTAINMENT_TOLERANCE
                <= self.value
                <= self.ci_high + _CONTAINMENT_TOLERANCE
            ):
                raise ValueError(
                    f"measurement {self.name!r} value {self.value} falls outside "
                    f"its Wilson interval [{self.ci_low}, {self.ci_high}]"
                )
        elif self.ci_method != CI_NONE:
            raise ValueError(
                f"measurement {self.name!r} names ci_method {self.ci_method!r} "
                "but supplies no interval"
            )

        if self.kind == KIND_PROPORTION and not 0.0 <= self.value <= 1.0:
            raise ValueError(
                f"proportion {self.name!r} must be in [0, 1], got {self.value}"
            )
        if self.successes is not None:
            if self.successes < 0:
                raise ValueError(f"successes must be non-negative for {self.name!r}")
            if self.successes > self.n:
                raise ValueError(
                    f"successes ({self.successes}) exceeds n ({self.n}) "
                    f"for {self.name!r}"
                )

    # -- constructors ---------------------------------------------------------

    @classmethod
    def proportion(
        cls,
        name: str,
        successes: int,
        n: int,
        *,
        confidence: float = DEFAULT_CONFIDENCE,
        method_note: str = "",
        direction: str = DIRECTION_NEUTRAL,
    ) -> "Measurement":
        """A rate with its Wilson interval attached.

        ``n == 0`` yields value 0.0 with the interval [0, 1] -- no evidence,
        stated as such. Check :attr:`is_informative` before drawing anything
        from it.
        """
        low, high = wilson_interval(successes, n, confidence)
        value = (successes / n) if n else 0.0
        return cls(
            name=name,
            kind=KIND_PROPORTION,
            value=value,
            n=n,
            ci_low=low,
            ci_high=high,
            ci_method=CI_WILSON,
            confidence=confidence,
            successes=successes,
            method_note=method_note,
            direction=direction,
        )

    @classmethod
    def count(
        cls,
        name: str,
        value: int,
        n: int,
        *,
        method_note: str = "",
        direction: str = DIRECTION_NEUTRAL,
    ) -> "Measurement":
        """A raw count out of a population -- e.g. exceptions noted."""
        return cls(
            name=name,
            kind=KIND_COUNT,
            value=float(value),
            n=n,
            method_note=method_note,
            direction=direction,
        )

    # -- properties -----------------------------------------------------------

    @property
    def is_informative(self) -> bool:
        """False when the sample cannot support any conclusion (n == 0)."""
        return self.n > 0

    @property
    def interval_width(self) -> Optional[float]:
        if self.ci_low is None or self.ci_high is None:
            return None
        return self.ci_high - self.ci_low

    # -- rendering ------------------------------------------------------------

    def render(self, *, as_percent: bool = False, places: int = 3) -> str:
        """Human-readable form that always carries n, and the interval if any.

        This is the only sanctioned way to put a measurement in front of a
        reader; report code calls it instead of formatting ``value`` directly,
        which is how the no-bare-percentages rule survives contact with the
        rendering layer.
        """
        if not self.is_informative:
            return f"not tested (n=0) [{self.name}]"

        if self.kind == KIND_COUNT:
            # "3 of 25" where the count is drawn from a population; a bare
            # tally (value == n) has no meaningful denominator to show.
            if int(self.value) == self.n:
                return str(int(self.value))
            return f"{int(self.value)} of {self.n}"

        def fmt(x: float) -> str:
            return f"{x * 100:.{max(places - 2, 1)}f}%" if as_percent else f"{x:.{places}f}"

        body = fmt(self.value)
        if self.ci_low is not None and self.ci_high is not None:
            pct = int(round((self.confidence or DEFAULT_CONFIDENCE) * 100))
            body += f" ({pct}% CI [{fmt(self.ci_low)}, {fmt(self.ci_high)}]"
            if self.successes is not None:
                body += f", {self.successes}/{self.n}"
            else:
                body += f", n={self.n}"
            body += ")"
        else:
            body += f" (n={self.n})"
        return body

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return f"{self.name}={self.render()}"

    # -- serialization --------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "value": self.value,
            "n": self.n,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "ci_method": self.ci_method,
            "confidence": self.confidence,
            "successes": self.successes,
            "method_note": self.method_note,
            "direction": self.direction,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Measurement":
        return cls(
            name=data["name"],
            kind=data["kind"],
            value=data["value"],
            n=data["n"],
            ci_low=data.get("ci_low"),
            ci_high=data.get("ci_high"),
            ci_method=data.get("ci_method", CI_NONE),
            confidence=data.get("confidence"),
            successes=data.get("successes"),
            method_note=data.get("method_note", ""),
            direction=data.get("direction", DIRECTION_NEUTRAL),
        )


@dataclass(frozen=True)
class ModelFingerprint:
    """Identifies exactly what was under test.

    "We tested the model" is not an audit statement; "we tested this model id
    through this adapter at these parameters" is. The system prompt is recorded
    as a hash rather than verbatim so fingerprints stay small and comparable
    while still detecting a changed prompt.
    """

    adapter: str
    model: str
    params: Dict[str, Any] = field(default_factory=dict)
    system_prompt_hash: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.adapter:
            raise ValueError("fingerprint requires an adapter name")
        if not self.model:
            raise ValueError("fingerprint requires a model identifier")
        _require_json(dict(self.params), "fingerprint params")

    def digest(self) -> str:
        """Stable hash over the whole fingerprint."""
        return content_hash(self.to_dict())

    def short(self) -> str:
        """First 12 hex characters of :meth:`digest`, for display."""
        return self.digest().split(":", 1)[1][:12]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "adapter": self.adapter,
            "model": self.model,
            "params": dict(self.params),
            "system_prompt_hash": self.system_prompt_hash,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ModelFingerprint":
        return cls(
            adapter=data["adapter"],
            model=data["model"],
            params=dict(data.get("params") or {}),
            system_prompt_hash=data.get("system_prompt_hash"),
        )


@dataclass(frozen=True)
class Trial:
    """One model call and its result -- a single item in the sample.

    Workpapers show the items tested, not just the aggregate, so exceptions can
    be inspected individually. Trials are therefore retained in full.
    """

    index: int
    prompt: str
    response_text: str
    system: Optional[str] = None
    latency_ms: float = 0.0
    #: Per-trial judgment where the probe makes one; None where it does not.
    passed: Optional[bool] = None
    #: Short probe-specific annotations, e.g. {"canary": "leaked"}.
    labels: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError(f"trial index must be non-negative, got {self.index}")
        _require_json(dict(self.labels), "trial labels")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "prompt": self.prompt,
            "response_text": self.response_text,
            "system": self.system,
            "latency_ms": self.latency_ms,
            "passed": self.passed,
            "labels": dict(self.labels),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Trial":
        return cls(
            index=data["index"],
            prompt=data["prompt"],
            response_text=data["response_text"],
            system=data.get("system"),
            latency_ms=data.get("latency_ms", 0.0),
            passed=data.get("passed"),
            labels=dict(data.get("labels") or {}),
        )


@dataclass(frozen=True)
class Evidence:
    """Everything one probe run produced, sufficient to re-derive its result."""

    probe_id: str
    outcome: str
    fingerprint: ModelFingerprint
    started_at: str
    finished_at: str
    trials: Tuple[Trial, ...] = ()
    measurements: Tuple[Measurement, ...] = ()
    #: The probe configuration used, so the run can be reproduced.
    config: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.probe_id:
            raise ValueError("evidence requires a probe_id")
        if self.outcome not in OUTCOMES:
            raise ValueError(
                f"unknown outcome {self.outcome!r}; expected one of {sorted(OUTCOMES)}"
            )
        # Accept any sequence at the call site, store tuples.
        object.__setattr__(self, "trials", tuple(self.trials))
        object.__setattr__(self, "measurements", tuple(self.measurements))
        _require_json(dict(self.config), "evidence config")

        names = [m.name for m in self.measurements]
        if len(names) != len(set(names)):
            raise ValueError(
                f"duplicate measurement names in evidence for {self.probe_id!r}: {names}"
            )

    # -- accessors ------------------------------------------------------------

    def measurement(self, name: str) -> Optional[Measurement]:
        for m in self.measurements:
            if m.name == name:
                return m
        return None

    @property
    def primary(self) -> Optional[Measurement]:
        """The headline measurement -- by convention, the first one recorded."""
        return self.measurements[0] if self.measurements else None

    @property
    def sample_size(self) -> int:
        return len(self.trials)

    @property
    def exceptions(self) -> Tuple[Trial, ...]:
        """Trials the probe judged as failing -- the items a reviewer inspects."""
        return tuple(t for t in self.trials if t.passed is False)

    def with_outcome(self, outcome: str, *, notes: str = "") -> "Evidence":
        return replace(self, outcome=outcome, notes=notes or self.notes)

    def summary(self) -> str:
        head = f"[{self.outcome.upper():>12}] {self.probe_id}"
        if self.primary is not None:
            head += f" -- {self.primary.name}: {self.primary.render()}"
        return head

    # -- serialization --------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "probe_id": self.probe_id,
            "outcome": self.outcome,
            "fingerprint": self.fingerprint.to_dict(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "trials": [t.to_dict() for t in self.trials],
            "measurements": [m.to_dict() for m in self.measurements],
            "config": dict(self.config),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Evidence":
        version = data.get("schema_version", SCHEMA_VERSION)
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"evidence schema version {version} is newer than this build "
                f"understands ({SCHEMA_VERSION})"
            )
        return cls(
            probe_id=data["probe_id"],
            outcome=data["outcome"],
            fingerprint=ModelFingerprint.from_dict(data["fingerprint"]),
            started_at=data["started_at"],
            finished_at=data["finished_at"],
            trials=tuple(Trial.from_dict(t) for t in data.get("trials", ())),
            measurements=tuple(
                Measurement.from_dict(m) for m in data.get("measurements", ())
            ),
            config=dict(data.get("config") or {}),
            notes=data.get("notes", ""),
            schema_version=version,
        )

    def content_hash(self) -> str:
        """Hash of this record, used by the journal's chain."""
        return content_hash(self.to_dict())


def evidence_hashes(items: Iterable[Evidence]) -> Sequence[str]:
    """Content hashes for a run of evidence, in order."""
    return [e.content_hash() for e in items]
