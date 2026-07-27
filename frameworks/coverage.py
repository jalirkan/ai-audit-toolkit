"""Which controls a run produced evidence for, and -- more usefully -- which it did not.

The gaps are the point. A coverage report that only listed what was tested
would flatter the engagement; the value is in naming the controls in scope that
nothing here speaks to, so nobody mistakes a green run for a complete audit.

Coverage separates two things that are easy to conflate:

- **No evidence** -- nothing in this run addressed the control at all.
- **Evidence, with exceptions** -- a procedure ran and found problems. The
  control is *covered* and *failing*. Reporting that as "covered" without the
  outcome would be worse than reporting nothing.

Procedural capabilities (the journal, drift monitoring, generated workpapers)
produce evidence with no pass or fail, so they get their own status. A journal
existing shows the technical means are in place; it is not a test result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from battery.runner import BatteryResult
from core.evidence import (
    OUTCOME_ERROR,
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
)
from frameworks.catalog import (
    Control,
    ControlReference,
    Framework,
    MappingSet,
    capability_source,
    load_frameworks,
    load_mappings,
    probe_source,
)

STATUS_NO_EVIDENCE = "no-evidence"
#: Only non-probe capabilities apply: the means exist, but nothing was tested.
STATUS_EVIDENCE_PRESENT = "evidence-present"
STATUS_TESTED_PASS = "tested-pass"
STATUS_TESTED_INCONCLUSIVE = "tested-inconclusive"
STATUS_TESTED_ERROR = "tested-error"
STATUS_TESTED_EXCEPTIONS = "tested-with-exceptions"

#: Mirrors the battery rollup (D-016): a deficiency leads.
_OUTCOME_TO_STATUS = {
    OUTCOME_FAIL: STATUS_TESTED_EXCEPTIONS,
    OUTCOME_ERROR: STATUS_TESTED_ERROR,
    OUTCOME_INCONCLUSIVE: STATUS_TESTED_INCONCLUSIVE,
    OUTCOME_PASS: STATUS_TESTED_PASS,
}
_STATUS_PRECEDENCE = (
    STATUS_TESTED_EXCEPTIONS,
    STATUS_TESTED_ERROR,
    STATUS_TESTED_INCONCLUSIVE,
    STATUS_TESTED_PASS,
)

__all__ = [
    "STATUS_NO_EVIDENCE",
    "STATUS_EVIDENCE_PRESENT",
    "STATUS_TESTED_PASS",
    "STATUS_TESTED_INCONCLUSIVE",
    "STATUS_TESTED_ERROR",
    "STATUS_TESTED_EXCEPTIONS",
    "ControlCoverage",
    "FrameworkCoverage",
    "CoverageReport",
    "build_coverage",
]


@dataclass(frozen=True)
class ControlCoverage:
    """One control, and what this run had to say about it."""

    framework_id: str
    control: Control
    status: str
    #: Probe ids that produced evidence relevant to this control.
    probe_ids: Tuple[str, ...] = ()
    #: Capability names that apply.
    capabilities: Tuple[str, ...] = ()
    #: The arguments for each mapping, for a reviewer to challenge.
    references: Tuple[ControlReference, ...] = ()
    #: Outcome counts across the contributing evidence.
    outcome_counts: Dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.outcome_counts is None:
            object.__setattr__(self, "outcome_counts", {})

    @property
    def has_evidence(self) -> bool:
        return self.status != STATUS_NO_EVIDENCE

    @property
    def is_gap(self) -> bool:
        return self.status == STATUS_NO_EVIDENCE

    @property
    def needs_attention(self) -> bool:
        return self.status in (STATUS_TESTED_EXCEPTIONS, STATUS_TESTED_ERROR)

    def to_dict(self) -> Dict[str, object]:
        return {
            "framework": self.framework_id,
            "control_id": self.control.id,
            "summary": self.control.summary,
            "topic": self.control.topic,
            "status": self.status,
            "probe_ids": list(self.probe_ids),
            "capabilities": list(self.capabilities),
            "outcome_counts": dict(self.outcome_counts),
            "references": [r.to_dict() for r in self.references],
        }


@dataclass(frozen=True)
class FrameworkCoverage:
    """Coverage across one framework's catalog."""

    framework: Framework
    controls: Tuple[ControlCoverage, ...]

    @property
    def gaps(self) -> Tuple[ControlCoverage, ...]:
        return tuple(c for c in self.controls if c.is_gap)

    @property
    def covered(self) -> Tuple[ControlCoverage, ...]:
        return tuple(c for c in self.controls if c.has_evidence)

    @property
    def needing_attention(self) -> Tuple[ControlCoverage, ...]:
        return tuple(c for c in self.controls if c.needs_attention)

    def counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for c in self.controls:
            counts[c.status] = counts.get(c.status, 0) + 1
        return counts

    def summary_lines(self) -> List[str]:
        lines = [f"{self.framework.citation()}"]
        lines.append(
            f"  {len(self.covered)} of {len(self.controls)} catalogued controls "
            f"have evidence from this run"
        )
        for control in self.controls:
            marker = {
                STATUS_TESTED_PASS: "ok  ",
                STATUS_TESTED_EXCEPTIONS: "FAIL",
                STATUS_TESTED_ERROR: "ERR ",
                STATUS_TESTED_INCONCLUSIVE: "??  ",
                STATUS_EVIDENCE_PRESENT: "doc ",
                STATUS_NO_EVIDENCE: "GAP ",
            }[control.status]
            sources = ", ".join(control.probe_ids + control.capabilities) or "-"
            lines.append(f"    [{marker}] {control.control.id}: {sources}")
        return lines

    def to_dict(self) -> Dict[str, object]:
        return {
            "framework": self.framework.to_dict(),
            "counts": self.counts(),
            "controls": [c.to_dict() for c in self.controls],
        }


@dataclass(frozen=True)
class CoverageReport:
    """Coverage across every catalogued framework."""

    frameworks: Tuple[FrameworkCoverage, ...]
    active_probe_ids: Tuple[str, ...] = ()
    active_capabilities: Tuple[str, ...] = ()
    #: Mapped sources that were not active in this run, so their controls are
    #: gaps that a fuller run could close.
    inactive_sources: Tuple[str, ...] = ()

    @property
    def all_gaps(self) -> Tuple[ControlCoverage, ...]:
        return tuple(c for f in self.frameworks for c in f.gaps)

    @property
    def all_needing_attention(self) -> Tuple[ControlCoverage, ...]:
        return tuple(c for f in self.frameworks for c in f.needing_attention)

    def for_framework(self, framework_id: str) -> Optional[FrameworkCoverage]:
        for f in self.frameworks:
            if f.framework.id == framework_id:
                return f
        return None

    def summary_lines(self) -> List[str]:
        lines: List[str] = [
            "Framework coverage. A mapping means the procedure produced evidence "
            "relevant to the control, never that the control is satisfied.",
        ]
        for framework in self.frameworks:
            lines.extend(framework.summary_lines())
        if self.all_gaps:
            lines.append(
                f"  {len(self.all_gaps)} catalogued control(s) have no evidence "
                "from this run:"
            )
            for gap in self.all_gaps:
                lines.append(f"    {gap.framework_id} {gap.control.id}")
        return lines

    def to_dict(self) -> Dict[str, object]:
        return {
            "active_probe_ids": list(self.active_probe_ids),
            "active_capabilities": list(self.active_capabilities),
            "inactive_sources": list(self.inactive_sources),
            "frameworks": [f.to_dict() for f in self.frameworks],
        }


def build_coverage(
    result: Optional[BatteryResult] = None,
    *,
    probe_ids: Optional[Sequence[str]] = None,
    capabilities: Sequence[str] = (),
    frameworks: Optional[Dict[str, Framework]] = None,
    mappings: Optional[MappingSet] = None,
) -> CoverageReport:
    """Build a coverage report.

    Args:
        result: a battery run. Probe outcomes come from its evidence.
        probe_ids: probes to treat as active when no ``result`` is supplied --
            useful for asking "what would this suite cover?" before running it.
        capabilities: capabilities in use, e.g. ``("evidence-journal",)``. The
            caller declares these because the toolkit cannot tell from a result
            whether a journal was written or a drift comparison performed.
        frameworks: catalogs to report against. Defaults to the bundled ones.
        mappings: source-to-control mapping. Defaults to the bundled one.
    """
    catalogs = frameworks if frameworks is not None else load_frameworks()
    mapping_set = mappings if mappings is not None else load_mappings()

    outcomes_by_probe: Dict[str, List[str]] = {}
    if result is not None:
        for evidence in result.evidence:
            outcomes_by_probe.setdefault(evidence.probe_id, []).append(evidence.outcome)
        active_probes = sorted(outcomes_by_probe)
    else:
        active_probes = sorted(probe_ids or ())

    active_capabilities = sorted(set(capabilities))
    active_sources = {probe_source(p) for p in active_probes} | {
        capability_source(c) for c in active_capabilities
    }
    inactive_sources = tuple(
        sorted(s for s in mapping_set.by_source if s not in active_sources)
    )

    framework_coverages: List[FrameworkCoverage] = []
    for framework_id, framework in sorted(catalogs.items()):
        control_coverages: List[ControlCoverage] = []
        for control in framework.controls:
            sources = mapping_set.sources_for(framework_id, control.id)
            contributing_probes = tuple(
                p for p in active_probes if probe_source(p) in sources
            )
            contributing_capabilities = tuple(
                c for c in active_capabilities if capability_source(c) in sources
            )
            references = tuple(
                r
                for source in sources
                if source in active_sources
                for r in mapping_set.references_for(source)
                if r.framework == framework_id and r.control_id == control.id
            )

            counts: Dict[str, int] = {}
            for probe_id in contributing_probes:
                for outcome in outcomes_by_probe.get(probe_id, []):
                    counts[outcome] = counts.get(outcome, 0) + 1

            if not contributing_probes and not contributing_capabilities:
                status = STATUS_NO_EVIDENCE
            elif not contributing_probes:
                status = STATUS_EVIDENCE_PRESENT
            elif not counts:
                # Probes named but no run supplied: a projection of coverage.
                status = STATUS_EVIDENCE_PRESENT
            else:
                status = next(
                    s
                    for s in _STATUS_PRECEDENCE
                    if s in {_OUTCOME_TO_STATUS[o] for o in counts}
                )

            control_coverages.append(
                ControlCoverage(
                    framework_id=framework_id,
                    control=control,
                    status=status,
                    probe_ids=contributing_probes,
                    capabilities=contributing_capabilities,
                    references=references,
                    outcome_counts=counts,
                )
            )
        framework_coverages.append(
            FrameworkCoverage(framework=framework, controls=tuple(control_coverages))
        )

    return CoverageReport(
        frameworks=tuple(framework_coverages),
        active_probe_ids=tuple(active_probes),
        active_capabilities=tuple(active_capabilities),
        inactive_sources=inactive_sources,
    )
