"""Rendering a multi-endpoint comparison.

Built from the same document model as the workpapers, so it renders to Markdown
and standalone HTML without a second code path.

The layout is deliberate: outcomes first, then each metric with its interval on
every endpoint, then the metrics no endpoint was distinguished on, then the
operational figures. The "not distinguished" section exists because a reader
handed a table of point estimates will rank them, and on overlapping intervals
that ranking is invented.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from compare.matrix import ComparisonMatrix
from report.document import (
    Callout,
    Document,
    Fields,
    Paragraph,
    Section,
    Table,
)
from report.workpaper import SCOPE_CAVEAT

NO_RANKING_NOTE = (
    "No overall ranking is given. A leak rate and an agreement rate are not "
    "commensurable, so an aggregate of them would not be a quantity, and which "
    "of them matters depends on the workload the endpoint is being considered "
    "for. That judgment belongs to the reader."
)

__all__ = ["NO_RANKING_NOTE", "build_comparison_report"]


def build_comparison_report(
    matrix: ComparisonMatrix,
    *,
    prepared_by: str = "",
    seed: int = 20260727,
) -> Document:
    labels = matrix.labels

    endpoint_rows = [
        (
            endpoint.label,
            endpoint.result.fingerprint.model,
            endpoint.result.outcome,
            str(endpoint.total_calls),
            endpoint.description,
        )
        for endpoint in matrix.endpoints
    ]

    outcome_rows = [
        (
            probe_id,
            unit or "-",
            *[matrix.outcome(label, (probe_id, unit)) for label in labels],
        )
        for probe_id, unit in matrix.units
    ]

    metric_rows = [
        (
            f"{row.probe_id} / {row.unit or '-'}",
            row.metric,
            *[row.rendered(label) for label in labels],
        )
        for row in matrix.metric_rows()
    ]

    sections: List[Section] = [
        Section(
            "Endpoints compared",
            (
                Table(
                    ("Label", "Model", "Overall", "Calls", "Adapter"),
                    tuple(endpoint_rows),
                ),
                Callout(NO_RANKING_NOTE, kind="warning"),
                Paragraph(SCOPE_CAVEAT),
            ),
            level=2,
        ),
        Section(
            "Outcomes by procedure",
            (Table(("Procedure", "Unit", *labels), tuple(outcome_rows)),),
            level=2,
        ),
        Section(
            "Measurements",
            (
                Paragraph(
                    "Every figure carries its confidence interval and the "
                    "number of items examined. Where two intervals overlap, "
                    "the run has not shown the endpoints to differ."
                ),
                Table(("Procedure / unit", "Measure", *labels), tuple(metric_rows)),
            ),
            level=2,
        ),
    ]

    undistinguished = matrix.undistinguished_metrics()
    if undistinguished:
        sections.append(
            Section(
                "Metrics that did not separate the endpoints",
                (
                    Paragraph(
                        "On these metrics every endpoint's interval overlaps "
                        "every other's. Ordering them by point estimate would "
                        "be inventing a difference the sample does not "
                        "support; a larger population would be needed to "
                        "distinguish them."
                    ),
                    Table(
                        ("Procedure", "Unit", "Measure"),
                        tuple(
                            (row.probe_id, row.unit or "-", row.metric)
                            for row in undistinguished
                        ),
                    ),
                ),
                level=2,
            )
        )

    latency_rows = []
    for endpoint in matrix.endpoints:
        measurement = endpoint.latency_measurement(seed=seed)
        latency_rows.append(
            (
                endpoint.label,
                str(endpoint.total_calls),
                measurement.render() if measurement else "not measured",
            )
        )
    sections.append(
        Section(
            "Operational figures",
            (
                Table(("Endpoint", "Calls", "Mean latency (ms)"), tuple(latency_rows)),
                Paragraph(
                    "Prices are deliberately absent: they change, they vary by "
                    "contract, and a stale rate baked into an audit artifact is "
                    "worse than none. Multiply calls by your own rate card."
                ),
            ),
            level=2,
        )
    )

    meta: List[Tuple[str, str]] = [("Battery", matrix.battery)]
    if prepared_by:
        meta.append(("Prepared by", prepared_by))

    return Document(
        title=f"Endpoint comparison — {matrix.battery}",
        subtitle=(
            "The same assurance battery run against each candidate endpoint."
        ),
        meta=tuple(meta),
        sections=tuple(sections),
        footer=(
            "Generated by ai-audit-toolkit. Educational and professional "
            "tooling; not a substitute for a qualified audit."
        ),
    )
