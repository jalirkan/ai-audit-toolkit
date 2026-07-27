"""Workpaper rendering: one section per unit tested, in audit form.

A workpaper has to let a reviewer who was not there re-perform the judgment.
So each section states, in order: the procedure performed, the population and
how the sample was drawn from it, the result with its uncertainty, the criterion
applied, the exceptions individually, the limitations of the method, and the
conclusion. Then the provenance -- which model, which run, which evidence hash
-- so the section can be tied back to the journal.

Two things the renderer refuses to do:

**No bare rates.** Every measured value is rendered through
``Measurement.render()``, which carries its interval and sample size. A test
asserts that no line containing a percent sign lacks an interval.

**No population inflation.** These procedures test every item in a configured
battery, which is a complete examination of a population the auditor chose --
not a random sample of some larger universe. The workpaper says exactly that,
because "sample of 22" implies a sampling frame that does not exist here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from battery.runner import BatteryResult
from core.evidence import (
    OUTCOME_ERROR,
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
    Evidence,
    Trial,
)
from frameworks.catalog import (
    Framework,
    MappingSet,
    load_frameworks,
    load_mappings,
    probe_source,
)
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

#: Prompts and responses are quoted in full in the journal; the workpaper shows
#: enough to recognise them without becoming unreadable.
EXCERPT_CHARS = 320

#: Shared by the workpapers and the letter, so it is phrased for both.
SCOPE_CAVEAT = (
    "This report records technical procedures performed against a model "
    "endpoint. It is evidence about observed behaviour under specific inputs. "
    "It is not an assessment of the governance, policy, data handling, or "
    "human oversight around the system, and it does not on its own establish "
    "that any control or legal obligation is satisfied."
)

__all__ = ["EXCERPT_CHARS", "SCOPE_CAVEAT", "build_workpapers", "excerpt"]


def excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    """Single-line excerpt, marked when truncated."""
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


def _probe_meta(probe_id: str) -> Dict[str, str]:
    probe_cls = PROBES.get(probe_id)
    if probe_cls is None:
        return {
            "title": probe_id,
            "procedure": "Procedure metadata unavailable: this probe is not "
            "registered in the build that rendered the report.",
            "population": "",
            "limitations": "",
            "remediation": "",
        }
    return {
        "title": probe_cls.title or probe_id,
        "procedure": probe_cls.procedure,
        "population": probe_cls.population,
        "limitations": probe_cls.limitations,
        "remediation": probe_cls.remediation,
    }


def _outcome_phrase(outcome: str) -> str:
    return {
        OUTCOME_PASS: "Pass — no exceptions requiring report",
        OUTCOME_FAIL: "Fail — exceptions noted",
        OUTCOME_INCONCLUSIVE: "Inconclusive — evidence insufficient to conclude",
        OUTCOME_ERROR: "Error — procedure could not be completed",
    }.get(outcome, outcome)


def _criterion_text(evidence: Evidence) -> str:
    rule = evidence.config.get("decision_rule", "")
    threshold = evidence.config.get("decision_threshold")
    direction = evidence.config.get("decision_direction", "")
    if rule == RULE_ZERO_TOLERANCE:
        minimum = evidence.config.get("min_sample")
        return (
            "Attribute sampling, zero tolerance: any exception fails the "
            "control; a pass additionally requires at least "
            f"{minimum} items examined."
        )
    if not rule:
        return "Not recorded."
    comparison = (
        "at or below the tolerance"
        if direction == "lower_is_better"
        else "at or above the required minimum"
    )
    return (
        f"Interval comparison: the conclusion follows only if the whole "
        f"confidence interval lies {comparison} of {threshold:.3f}. An interval "
        "spanning the threshold is reported as inconclusive rather than "
        "resolved in either direction."
    )


def _measurement_rows(evidence: Evidence) -> List[Tuple[str, ...]]:
    rows: List[Tuple[str, ...]] = []
    decided = evidence.config.get("decision_metric") or (
        evidence.primary.name if evidence.primary else ""
    )
    for measurement in evidence.measurements:
        rows.append(
            (
                measurement.name,
                measurement.render(),
                "yes" if measurement.name == decided else "",
                measurement.method_note or "",
            )
        )
    return rows


def _exception_rows(trials: Sequence[Trial]) -> List[Tuple[str, ...]]:
    rows = []
    for trial in trials:
        labels = ", ".join(
            f"{k}={v}"
            for k, v in sorted(trial.labels.items())
            if not isinstance(v, (list, dict))
        )
        rows.append(
            (
                str(trial.index),
                excerpt(trial.prompt, 160),
                excerpt(trial.response_text, 160),
                labels,
            )
        )
    return rows


def _framework_bullets(
    probe_id: str, frameworks: Dict[str, Framework], mappings: MappingSet
) -> List[str]:
    bullets: List[str] = []
    for reference in mappings.references_for(probe_source(probe_id)):
        framework = frameworks.get(reference.framework)
        name = framework.name if framework else reference.framework
        bullets.append(
            f"{name} — {reference.control_id}: {reference.rationale}"
        )
    return bullets


def _unit_section(
    reference_id: str,
    evidence: Evidence,
    frameworks: Dict[str, Framework],
    mappings: MappingSet,
) -> Section:
    meta = _probe_meta(evidence.probe_id)
    unit = evidence.config.get("unit", "")
    title = f"{reference_id} — {meta['title']}"
    if unit:
        title += f" — {unit}"

    subsections: List[Section] = [
        Section(
            "Procedure performed",
            (Paragraph(meta["procedure"]),),
            level=4,
        ),
        Section(
            "Population and examination",
            (
                Fields(
                    (
                        ("Population", meta["population"] or "Not recorded."),
                        ("Items examined", str(evidence.sample_size)),
                        (
                            "Basis of selection",
                            "Complete examination: every item in the configured "
                            "population was tested. The population is the battery "
                            "as configured by the auditor, which is a judgmental "
                            "selection and not a random sample of all possible "
                            "inputs. Intervals reported below describe sampling "
                            "variability in the model's responses, not "
                            "generalisation beyond this population.",
                        ),
                    )
                ),
            ),
            level=4,
        ),
        Section(
            "Result",
            (
                Table(
                    ("Measure", "Result", "Conclusion drawn on", "Method"),
                    tuple(_measurement_rows(evidence)),
                ),
                Fields((("Criterion applied", _criterion_text(evidence)),)),
            ),
            level=4,
        ),
    ]

    exceptions = evidence.exceptions
    if exceptions:
        subsections.append(
            Section(
                f"Exceptions ({len(exceptions)})",
                (
                    Paragraph(
                        "Each item below is reproduced in full in the evidence "
                        "journal; excerpts are shown here for readability."
                    ),
                    Table(
                        ("Item", "Input", "Response", "Detail"),
                        tuple(_exception_rows(exceptions)),
                    ),
                ),
                level=4,
            )
        )
    else:
        subsections.append(
            Section(
                "Exceptions",
                (Paragraph("No exceptions noted."),),
                level=4,
            )
        )

    if meta["limitations"]:
        subsections.append(
            Section(
                "Limitations of this procedure",
                (Callout(meta["limitations"], kind="warning"),),
                level=4,
            )
        )

    subsections.append(
        Section("Conclusion", (Paragraph(evidence.notes or "Not recorded."),), level=4)
    )

    bullets = _framework_bullets(evidence.probe_id, frameworks, mappings)
    if bullets:
        subsections.append(
            Section(
                "Framework references",
                (
                    Paragraph(
                        "Each reference asserts that this procedure produces "
                        "evidence relevant to the control. None asserts that "
                        "the control is satisfied."
                    ),
                    Bullets(tuple(bullets)),
                ),
                level=4,
            )
        )

    header = Fields(
        (
            ("Reference", reference_id),
            ("Probe", evidence.probe_id),
            ("Unit tested", unit or "—"),
            ("Conclusion", _outcome_phrase(evidence.outcome)),
            (
                "Model tested",
                f"{evidence.fingerprint.adapter}:{evidence.fingerprint.model} "
                f"(fingerprint {evidence.fingerprint.short()})",
            ),
            (
                "Parameters",
                ", ".join(
                    f"{k}={v}" for k, v in sorted(evidence.fingerprint.params.items())
                )
                or "—",
            ),
            ("Performed", f"{evidence.started_at} to {evidence.finished_at}"),
            ("Evidence hash", evidence.content_hash()),
        )
    )

    return Section(title, (header,), level=3, subsections=tuple(subsections))


def build_workpapers(
    result: BatteryResult,
    *,
    frameworks: Optional[Dict[str, Framework]] = None,
    mappings: Optional[MappingSet] = None,
    prepared_by: str = "",
    journal_head: str = "",
) -> Document:
    """Build the workpaper document for a battery run."""
    catalogs = frameworks if frameworks is not None else load_frameworks()
    mapping_set = mappings if mappings is not None else load_mappings()

    counts = result.outcome_counts
    tally = ", ".join(f"{n} {name}" for name, n in sorted(counts.items()) if n)

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
    if prepared_by:
        meta.append(("Prepared by", prepared_by))
    if journal_head:
        meta.append(("Journal head hash", journal_head))

    summary_blocks: List[Any] = [
        Fields(
            (
                ("Units tested", str(result.units_tested)),
                ("Total model calls", str(result.total_trials)),
                ("Outcomes", tally or "none"),
                ("Overall", _outcome_phrase(result.outcome)),
            )
        ),
        Callout(SCOPE_CAVEAT, kind="warning"),
    ]
    if result.description:
        summary_blocks.insert(0, Paragraph(result.description))

    if journal_head:
        summary_blocks.append(
            Paragraph(
                "The evidence journal head hash is recorded above. Verifying the "
                "chain proves the recorded evidence has not been altered since "
                "it was written; it does not prove the journal was not rebuilt "
                "wholesale, which is why the head should be retained "
                "independently of the database file."
            )
        )

    sections: List[Section] = [
        Section("Summary", tuple(summary_blocks), level=2),
        Section(
            "Index of workpapers",
            (
                Table(
                    ("Reference", "Procedure", "Unit", "Conclusion", "Items"),
                    tuple(
                        (
                            f"WP-{i:02d}",
                            _probe_meta(e.probe_id)["title"],
                            str(e.config.get("unit", "—")),
                            e.outcome,
                            str(e.sample_size),
                        )
                        for i, e in enumerate(result.evidence, start=1)
                    ),
                ),
            ),
            level=2,
        ),
    ]

    detail = Section(
        "Workpapers",
        (),
        level=2,
        subsections=tuple(
            _unit_section(f"WP-{i:02d}", evidence, catalogs, mapping_set)
            for i, evidence in enumerate(result.evidence, start=1)
        ),
    )
    sections.append(detail)

    return Document(
        title=f"Audit workpapers — {result.battery}",
        subtitle=(
            "Technical assurance procedures performed against an AI model "
            "endpoint."
        ),
        meta=tuple(meta),
        sections=tuple(sections),
        footer=(
            "Generated by ai-audit-toolkit. Every measured rate is reported with "
            "a confidence interval and the number of items examined. Educational "
            "and professional tooling; not a substitute for a qualified audit."
        ),
    )
