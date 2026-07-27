"""Management-letter rendering: findings, ranked, with what to do about them.

Where the workpaper is exhaustive, the letter is selective. It answers what a
reader with limited time needs: what went wrong, how badly, what it means, and
what to do.

## Findings and observations are different things

A **finding** is a deficiency: a procedure ran, concluded, and found the
control wanting. An **observation** is a scope limitation: a procedure could
not conclude, because the sample was too small or the endpoint failed. Listing
the second among the first would inflate the severity of the report and mislead
the reader about what was actually established. They get separate sections.

## Severity is a stated rule, not a feeling

- **High** — a control that admits no exceptions failed at all, or a rate
  failed with its whole interval at twice the tolerance or worse.
- **Medium** — any other failure to meet the criterion.

The rule is printed in the letter. A severity a reader cannot reconstruct is a
severity they cannot challenge, and one they cannot challenge is not worth
much.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from battery.runner import BatteryResult
from core.evidence import (
    OUTCOME_ERROR,
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    Evidence,
)
from frameworks.catalog import Framework, MappingSet, load_frameworks, load_mappings, probe_source
from frameworks.coverage import CoverageReport, build_coverage
from probes.base import PROBES, RULE_ZERO_TOLERANCE
from report.document import (
    Bullets,
    Callout,
    Document,
    Fields,
    Paragraph,
    Section,
    Table,
)
from report.workpaper import SCOPE_CAVEAT, excerpt

SEVERITY_HIGH = "High"
SEVERITY_MEDIUM = "Medium"
_SEVERITY_ORDER = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 1}

#: A failure this far past tolerance is high severity regardless of the rule.
HIGH_SEVERITY_MULTIPLE = 2.0

SEVERITY_BASIS = (
    "High: a control that admits no exceptions recorded at least one, or a "
    "measured rate whose entire confidence interval sits at twice the "
    "tolerance or worse. Medium: any other failure to meet the stated "
    "criterion. Scope limitations are listed separately and are not assigned a "
    "severity, because nothing was established about the control either way."
)

__all__ = [
    "SEVERITY_HIGH",
    "SEVERITY_MEDIUM",
    "SEVERITY_BASIS",
    "Finding",
    "severity_for",
    "build_findings",
    "build_letter",
]


@dataclass(frozen=True)
class Finding:
    """One deficiency, ranked and explained."""

    reference: str
    severity: str
    evidence: Evidence
    title: str
    rationale: str

    @property
    def probe_id(self) -> str:
        return self.evidence.probe_id

    @property
    def unit(self) -> str:
        return str(self.evidence.config.get("unit", ""))

    def sort_key(self) -> Tuple[int, float]:
        primary = self.evidence.primary
        # Worst first within a severity band: the higher the lower bound of a
        # bad rate, the more certainly the problem is real.
        magnitude = primary.ci_low if primary and primary.ci_low is not None else 0.0
        return (_SEVERITY_ORDER.get(self.severity, 9), -magnitude)


def severity_for(evidence: Evidence) -> Tuple[str, str]:
    """Return ``(severity, why)`` for a failed procedure."""
    rule = evidence.config.get("decision_rule", "")
    threshold = evidence.config.get("decision_threshold", 0.0) or 0.0
    primary = evidence.primary

    if rule == RULE_ZERO_TOLERANCE:
        count = primary.successes if primary and primary.successes is not None else 0
        return (
            SEVERITY_HIGH,
            f"The control admits no exceptions and {count} were recorded.",
        )

    if primary is not None and primary.ci_low is not None and threshold > 0:
        if primary.ci_low >= threshold * HIGH_SEVERITY_MULTIPLE:
            return (
                SEVERITY_HIGH,
                "The entire interval sits at or beyond twice the tolerance of "
                f"{threshold:.3f}.",
            )

    return (
        SEVERITY_MEDIUM,
        f"The measured rate failed the stated criterion of {threshold:.3f}, "
        "without reaching the threshold for high severity.",
    )


def _probe_title(probe_id: str) -> str:
    probe_cls = PROBES.get(probe_id)
    return (probe_cls.title if probe_cls else "") or probe_id


def _remediation(probe_id: str) -> str:
    probe_cls = PROBES.get(probe_id)
    return (probe_cls.remediation if probe_cls else "") or (
        "No standing recommendation is recorded for this procedure; the "
        "exceptions should be reviewed individually."
    )


def build_findings(result: BatteryResult) -> List[Finding]:
    """Deficiencies from a run, ranked worst first."""
    findings: List[Finding] = []
    for index, evidence in enumerate(result.evidence, start=1):
        if evidence.outcome != OUTCOME_FAIL:
            continue
        severity, rationale = severity_for(evidence)
        unit = evidence.config.get("unit", "")
        title = _probe_title(evidence.probe_id)
        if unit:
            title += f" — {unit}"
        findings.append(
            Finding(
                reference=f"WP-{index:02d}",
                severity=severity,
                evidence=evidence,
                title=title,
                rationale=rationale,
            )
        )
    return sorted(findings, key=Finding.sort_key)


def _finding_section(finding: Finding, mappings: MappingSet, frameworks: Dict[str, Framework]) -> Section:
    evidence = finding.evidence
    primary = evidence.primary
    blocks: List[Any] = [
        Fields(
            (
                ("Severity", finding.severity),
                ("Basis for severity", finding.rationale),
                ("Workpaper", finding.reference),
                (
                    "Measured",
                    primary.render() if primary else "no measurement recorded",
                ),
                ("Items examined", str(evidence.sample_size)),
                ("Exceptions", str(len(evidence.exceptions))),
            )
        ),
        Paragraph(evidence.notes or ""),
    ]

    examples = evidence.exceptions[:3]
    if examples:
        blocks.append(
            Table(
                ("Item", "Input", "Response"),
                tuple(
                    (str(t.index), excerpt(t.prompt, 140), excerpt(t.response_text, 140))
                    for t in examples
                ),
            )
        )
        if len(evidence.exceptions) > len(examples):
            blocks.append(
                Paragraph(
                    f"{len(evidence.exceptions)} exceptions in total; the "
                    f"first {len(examples)} are shown. All are reproduced in "
                    f"workpaper {finding.reference} and in the evidence journal."
                )
            )

    references = [
        f"{frameworks[r.framework].name if r.framework in frameworks else r.framework}"
        f" — {r.control_id}"
        for r in mappings.references_for(probe_source(evidence.probe_id))
    ]
    if references:
        blocks.append(
            Fields((("Relevant control references", "; ".join(references)),))
        )

    return Section(
        f"{finding.severity} — {finding.title}",
        # Drop any empty paragraph rather than rendering a blank line.
        tuple(b for b in blocks if not (isinstance(b, Paragraph) and not b.text)),
        level=3,
        subsections=(
            Section(
                "Recommendation",
                (Paragraph(_remediation(evidence.probe_id)),),
                level=4,
            ),
        ),
    )


def _observation_rows(result: BatteryResult) -> List[Tuple[str, ...]]:
    rows: List[Tuple[str, ...]] = []
    for index, evidence in enumerate(result.evidence, start=1):
        if evidence.outcome not in (OUTCOME_INCONCLUSIVE, OUTCOME_ERROR):
            continue
        rows.append(
            (
                f"WP-{index:02d}",
                _probe_title(evidence.probe_id),
                str(evidence.config.get("unit", "—")),
                "Not concluded"
                if evidence.outcome == OUTCOME_INCONCLUSIVE
                else "Procedure failed",
                excerpt(evidence.notes, 260),
            )
        )
    return rows


def build_letter(
    result: BatteryResult,
    *,
    coverage: Optional[CoverageReport] = None,
    frameworks: Optional[Dict[str, Framework]] = None,
    mappings: Optional[MappingSet] = None,
    addressee: str = "",
    prepared_by: str = "",
) -> Document:
    """Build the management-letter document for a battery run."""
    catalogs = frameworks if frameworks is not None else load_frameworks()
    mapping_set = mappings if mappings is not None else load_mappings()
    coverage_report = coverage if coverage is not None else build_coverage(
        result, frameworks=catalogs, mappings=mapping_set
    )

    findings = build_findings(result)
    observations = _observation_rows(result)

    meta: List[Tuple[str, str]] = [
        ("Battery", result.battery),
        ("Run", result.run_id),
        (
            "Model tested",
            f"{result.fingerprint.adapter}:{result.fingerprint.model} "
            f"(fingerprint {result.fingerprint.short()})",
        ),
        ("Performed", f"{result.started_at} to {result.finished_at}"),
    ]
    if addressee:
        meta.insert(0, ("To", addressee))
    if prepared_by:
        meta.append(("Prepared by", prepared_by))

    high = sum(1 for f in findings if f.severity == SEVERITY_HIGH)
    medium = len(findings) - high

    if findings:
        headline = (
            f"{len(findings)} finding(s) are reported below: {high} high and "
            f"{medium} medium severity."
        )
    else:
        headline = (
            "No findings are reported: every procedure that reached a "
            "conclusion did so without exceptions."
        )
    if observations:
        headline += (
            f" {len(observations)} procedure(s) did not reach a conclusion and "
            "are listed as scope limitations."
        )

    sections: List[Section] = [
        Section(
            "Basis and scope",
            (
                Paragraph(headline),
                Fields(
                    (
                        ("Units tested", str(result.units_tested)),
                        ("Total model calls", str(result.total_trials)),
                    )
                ),
                Callout(SCOPE_CAVEAT, kind="warning"),
                Paragraph(
                    "Every rate in this letter is reported with a confidence "
                    "interval and the number of items examined. A rate without "
                    "those two figures cannot be acted on, because it does not "
                    "distinguish one exception in eight from a hundred in eight "
                    "hundred."
                ),
            ),
            level=2,
        )
    ]

    if findings:
        sections.append(
            Section(
                "Findings",
                (Paragraph("Ranked by severity, then by how firmly the evidence establishes the problem."),),
                level=2,
                subsections=tuple(
                    _finding_section(f, mapping_set, catalogs) for f in findings
                ),
            )
        )
    else:
        sections.append(
            Section(
                "Findings",
                (
                    Paragraph(
                        "None. Note that this states no exceptions were found by "
                        "the procedures performed, which is a narrower claim than "
                        "the absence of the underlying weaknesses -- each "
                        "procedure's limitations are recorded in its workpaper."
                    ),
                ),
                level=2,
            )
        )

    if observations:
        sections.append(
            Section(
                "Scope limitations",
                (
                    Paragraph(
                        "These procedures did not establish anything about the "
                        "control in either direction. They are not findings, and "
                        "should not be read as clean results."
                    ),
                    Table(
                        ("Workpaper", "Procedure", "Unit", "Status", "Reason"),
                        tuple(observations),
                    ),
                ),
                level=2,
            )
        )

    gaps = coverage_report.all_gaps
    coverage_blocks: List[Any] = [
        Paragraph(
            "Control references indicate that a procedure produced evidence "
            "relevant to a control. They do not indicate that any control is "
            "satisfied. The catalogues used are partial by design."
        )
    ]
    if gaps:
        coverage_blocks.append(
            Paragraph(
                f"{len(gaps)} catalogued control(s) received no evidence from "
                "this run:"
            )
        )
        coverage_blocks.append(
            Table(
                ("Framework", "Control", "What it addresses"),
                tuple(
                    (
                        catalogs[g.framework_id].name
                        if g.framework_id in catalogs
                        else g.framework_id,
                        g.control.id,
                        g.control.summary,
                    )
                    for g in gaps
                ),
            )
        )
    else:
        coverage_blocks.append(
            Paragraph(
                "Every control in the catalogues used received some evidence "
                "from this run."
            )
        )
    sections.append(Section("Framework coverage and gaps", tuple(coverage_blocks), level=2))

    sections.append(
        Section(
            "Basis of severity",
            (Paragraph(SEVERITY_BASIS),),
            level=2,
        )
    )

    return Document(
        title=f"Management letter — {result.battery}",
        subtitle="Findings from technical assurance procedures performed against an AI model endpoint.",
        meta=tuple(meta),
        sections=tuple(sections),
        footer=(
            "Generated by ai-audit-toolkit. Supporting detail for every finding "
            "is in the corresponding workpaper and in the tamper-evident "
            "evidence journal. Educational and professional tooling; not a "
            "substitute for a qualified audit."
        ),
    )
