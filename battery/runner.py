"""Running a battery and rolling its evidence up to a run-level result.

## There is no composite score

It would be easy to average the probe rates into one number and call it an
assurance score. It would also be meaningless: a leak rate and a paraphrase
agreement rate measure different things on different populations, and their
mean is not a quantity. Worse, averaging hides the case the reader most needs
to see -- one control failing badly among several passing.

So the rollup is a distribution, not an average: counts by outcome, plus a
single battery outcome derived by precedence rather than arithmetic.

**fail** beats **error** beats **inconclusive** beats **pass**. A detected
deficiency is the most actionable thing a run can find, so it leads. A
procedure that could not complete comes next, because "we do not know" outranks
"we could not conclude". A battery is called clean only when every probe in it
concluded and passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from adapters.base import ModelAdapter
from battery.spec import BatterySpec
from core.canonical import content_hash
from core.evidence import (
    OUTCOME_ERROR,
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
    OUTCOMES,
    Evidence,
    ModelFingerprint,
    utc_now_iso,
)
from journal.store import Journal

SCHEMA_VERSION = 1

#: Highest precedence first. See the module docstring for the reasoning.
OUTCOME_PRECEDENCE: Tuple[str, ...] = (
    OUTCOME_FAIL,
    OUTCOME_ERROR,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
)

__all__ = [
    "SCHEMA_VERSION",
    "OUTCOME_PRECEDENCE",
    "BatteryResult",
    "run_battery",
]


def _roll_up(outcomes: Sequence[str]) -> str:
    for candidate in OUTCOME_PRECEDENCE:
        if candidate in outcomes:
            return candidate
    return OUTCOME_INCONCLUSIVE


@dataclass(frozen=True)
class BatteryResult:
    """Everything one battery run produced."""

    battery: str
    run_id: str
    started_at: str
    finished_at: str
    fingerprint: ModelFingerprint
    evidence: Tuple[Evidence, ...]
    description: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence))

    # -- rollup ---------------------------------------------------------------

    @property
    def outcome_counts(self) -> Dict[str, int]:
        counts = {outcome: 0 for outcome in sorted(OUTCOMES)}
        for e in self.evidence:
            counts[e.outcome] += 1
        return counts

    @property
    def outcome(self) -> str:
        """Battery-level outcome by precedence, not by averaging."""
        if not self.evidence:
            return OUTCOME_INCONCLUSIVE
        return _roll_up([e.outcome for e in self.evidence])

    @property
    def units_tested(self) -> int:
        return len(self.evidence)

    @property
    def total_trials(self) -> int:
        return sum(e.sample_size for e in self.evidence)

    def evidence_for(self, probe_id: str) -> Tuple[Evidence, ...]:
        return tuple(e for e in self.evidence if e.probe_id == probe_id)

    def failures(self) -> Tuple[Evidence, ...]:
        return tuple(e for e in self.evidence if e.outcome == OUTCOME_FAIL)

    def evidence_hashes(self) -> List[str]:
        return [e.content_hash() for e in self.evidence]

    # -- rendering ------------------------------------------------------------

    def summary_lines(self) -> List[str]:
        counts = self.outcome_counts
        header = (
            f"{self.battery} [{self.outcome.upper()}] "
            f"run {self.run_id} against {self.fingerprint.adapter}:"
            f"{self.fingerprint.model} ({self.fingerprint.short()})"
        )
        tally = "  ".join(
            f"{name}={counts[name]}" for name in sorted(counts) if counts[name]
        )
        lines = [header, f"  {self.units_tested} unit(s) tested -- {tally}"]
        lines.extend(f"  {e.summary()}" for e in self.evidence)
        return lines

    # -- serialization --------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Full record, including every piece of evidence.

        Used for drift baselines, which need the measurements themselves.
        """
        return {
            "schema_version": self.schema_version,
            "battery": self.battery,
            "description": self.description,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "fingerprint": self.fingerprint.to_dict(),
            "outcome": self.outcome,
            "outcome_counts": self.outcome_counts,
            "evidence": [e.to_dict() for e in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BatteryResult":
        version = data.get("schema_version", SCHEMA_VERSION)
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"battery result schema version {version} is newer than this "
                f"build understands ({SCHEMA_VERSION})"
            )
        return cls(
            battery=data["battery"],
            run_id=data["run_id"],
            started_at=data["started_at"],
            finished_at=data["finished_at"],
            fingerprint=ModelFingerprint.from_dict(data["fingerprint"]),
            evidence=tuple(Evidence.from_dict(e) for e in data.get("evidence", ())),
            description=data.get("description", ""),
            schema_version=version,
        )

    def run_record(self) -> Dict[str, Any]:
        """Compact manifest for the journal.

        Deliberately does not repeat the evidence, which is journaled in its
        own entries. It lists their content hashes instead, so the run is a
        manifest that binds specific evidence records to this run -- and a
        substituted record fails to match.
        """
        return {
            "schema_version": self.schema_version,
            "battery": self.battery,
            "description": self.description,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "fingerprint": self.fingerprint.to_dict(),
            "outcome": self.outcome,
            "outcome_counts": self.outcome_counts,
            "units_tested": self.units_tested,
            "total_trials": self.total_trials,
            "evidence_hashes": self.evidence_hashes(),
        }


def make_run_id(battery: str, fingerprint: ModelFingerprint, started_at: str) -> str:
    """Short identifier derived from what makes this run distinct.

    Derived rather than random so that re-deriving it from stored fields is
    possible, and so tests with a pinned clock are reproducible.
    """
    digest = content_hash(
        {
            "battery": battery,
            "fingerprint": fingerprint.digest(),
            "started_at": started_at,
        }
    )
    return digest.split(":", 1)[1][:16]


def run_battery(
    spec: BatterySpec,
    adapter: ModelAdapter,
    *,
    journal: Optional[Journal] = None,
    started_at: Optional[str] = None,
) -> BatteryResult:
    """Run every probe in ``spec`` against ``adapter``.

    Probes run through ``run_safely``, so an adapter failure in one probe is
    recorded as error evidence and the rest of the battery still runs. A run
    that stops at the first transport hiccup produces no evidence about
    anything else, which is the wrong trade for an audit procedure.

    When a ``journal`` is supplied, each evidence record is appended as it is
    produced and the run manifest last, so an interrupted run still leaves a
    trail of what was completed.
    """
    start = started_at or utc_now_iso()
    fingerprint = adapter.fingerprint()
    run_id = make_run_id(spec.name, fingerprint, start)

    collected: List[Evidence] = []
    for probe_spec in spec.probes:
        probe = probe_spec.build()
        for evidence in probe.run_safely(adapter):
            collected.append(evidence)
            if journal is not None:
                journal.append_evidence(evidence)

    result = BatteryResult(
        battery=spec.name,
        run_id=run_id,
        started_at=start,
        finished_at=utc_now_iso(),
        fingerprint=fingerprint,
        evidence=tuple(collected),
        description=spec.description,
    )

    if journal is not None:
        journal.append_run(result.run_record())

    return result
