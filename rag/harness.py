"""Run the golden RAG dataset against the citation screen (offline or live).

Screen-check mode never calls a model: it scores each ``gold_answer`` with
``assess_response`` and compares the result to the planted ``expect`` label.
Live mode builds a ``CitationCase`` and runs ``CitationFaithfulnessProbe``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from adapters.base import ModelAdapter
from core.evidence import (
    DIRECTION_HIGHER_IS_BETTER,
    Evidence,
    Measurement,
    utc_now_iso,
)
from probes.base import HIGHER_IS_BETTER, Decision, decide
from probes.citation import (
    CitationFaithfulnessProbe,
    assess_response,
)
from rag.dataset import (
    EXPECT_FAITHFUL,
    EXPECT_UNFAITHFUL,
    GoldenDataset,
    GoldenItem,
)

#: Minimum share of planted labels the screen must get right to pass.
DEFAULT_MIN_SCREEN_ACCURACY = 0.9
DEFAULT_MIN_SAMPLE = 20

METRIC_SCREEN_ACCURACY = "screen_accuracy"

__all__ = [
    "DEFAULT_MIN_SCREEN_ACCURACY",
    "DEFAULT_MIN_SAMPLE",
    "METRIC_SCREEN_ACCURACY",
    "ItemScreenResult",
    "ScreenCheckResult",
    "run_screen_check",
    "run_live",
]


@dataclass(frozen=True)
class ItemScreenResult:
    """One golden item after the lexical screen."""

    item_id: str
    expect: str
    flagged_unfaithful: bool
    correct: bool
    unsupported_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "expect": self.expect,
            "flagged_unfaithful": self.flagged_unfaithful,
            "correct": self.correct,
            "unsupported_count": self.unsupported_count,
        }


@dataclass(frozen=True)
class ScreenCheckResult:
    """Planted-signal check of the citation screen against gold answers."""

    dataset_id: str
    decision: Decision
    accuracy: Measurement
    true_positives: Measurement
    true_negatives: Measurement
    false_positives: Measurement
    false_negatives: Measurement
    items: Tuple[ItemScreenResult, ...]
    started_at: str
    finished_at: str

    @property
    def outcome(self) -> str:
        return self.decision.outcome

    def summary_lines(self) -> List[str]:
        lines = [
            f"rag screen-check [{self.outcome.upper()}] {self.dataset_id}",
            f"  {self.accuracy.name}: {self.accuracy.render()}",
            f"  {self.decision.rationale}",
            (
                f"  tp={int(self.true_positives.value)}  "
                f"tn={int(self.true_negatives.value)}  "
                f"fp={int(self.false_positives.value)}  "
                f"fn={int(self.false_negatives.value)}  "
                f"(n={self.accuracy.n})"
            ),
        ]
        misses = [i for i in self.items if not i.correct]
        if misses:
            lines.append("  mislabeled by the screen:")
            for miss in misses:
                lines.append(
                    f"    {miss.item_id}: expect={miss.expect} "
                    f"flagged_unfaithful={miss.flagged_unfaithful}"
                )
        return lines

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "outcome": self.outcome,
            "decision": {
                "outcome": self.decision.outcome,
                "rationale": self.decision.rationale,
                "rule": self.decision.rule,
                "threshold": self.decision.threshold,
                "direction": self.decision.direction,
            },
            "accuracy": self.accuracy.to_dict(),
            "true_positives": self.true_positives.to_dict(),
            "true_negatives": self.true_negatives.to_dict(),
            "false_positives": self.false_positives.to_dict(),
            "false_negatives": self.false_negatives.to_dict(),
            "items": [i.to_dict() for i in self.items],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _screen_item(item: GoldenItem, sources: Sequence[str]) -> ItemScreenResult:
    assessments = assess_response(item.gold_answer, sources)
    unsupported = sum(1 for a in assessments if a.is_exception)
    flagged = unsupported > 0
    if item.expect == EXPECT_UNFAITHFUL:
        correct = flagged
    elif item.expect == EXPECT_FAITHFUL:
        correct = not flagged
    else:  # pragma: no cover - validated at load
        raise ValueError(f"unknown expect {item.expect!r}")
    return ItemScreenResult(
        item_id=item.id,
        expect=item.expect,
        flagged_unfaithful=flagged,
        correct=correct,
        unsupported_count=unsupported,
    )


def run_screen_check(
    dataset: GoldenDataset,
    *,
    min_accuracy: float = DEFAULT_MIN_SCREEN_ACCURACY,
    min_sample: int = DEFAULT_MIN_SAMPLE,
    confidence: float = 0.95,
) -> ScreenCheckResult:
    """Score gold answers with the citation screen; no model calls."""
    if not 0.0 <= min_accuracy <= 1.0:
        raise ValueError("min_accuracy must be in [0, 1]")
    started_at = utc_now_iso()
    results = tuple(_screen_item(item, dataset.sources) for item in dataset.items)
    n = len(results)
    correct = sum(1 for r in results if r.correct)

    # Positive = unfaithful (the defect the screen is meant to catch).
    tp = sum(
        1
        for r in results
        if r.expect == EXPECT_UNFAITHFUL and r.flagged_unfaithful
    )
    tn = sum(
        1 for r in results if r.expect == EXPECT_FAITHFUL and not r.flagged_unfaithful
    )
    fp = sum(
        1 for r in results if r.expect == EXPECT_FAITHFUL and r.flagged_unfaithful
    )
    fn = sum(
        1
        for r in results
        if r.expect == EXPECT_UNFAITHFUL and not r.flagged_unfaithful
    )

    accuracy = Measurement.proportion(
        METRIC_SCREEN_ACCURACY,
        correct,
        n,
        confidence=confidence,
        method_note=(
            "Share of planted gold answers where the citation screen's "
            "faithful/unfaithful call matched the dataset label. The screen is "
            "lexical; this checks the screen against known cases, not a model."
        ),
        direction=DIRECTION_HIGHER_IS_BETTER,
    )
    decision = decide(
        accuracy,
        threshold=min_accuracy,
        direction=HIGHER_IS_BETTER,
        min_sample=min_sample,
    )
    count_note = (
        "Confusion counts treating 'unfaithful' as the positive class. "
        "Reported as tallies, not combined into a composite score."
    )
    finished_at = utc_now_iso()
    return ScreenCheckResult(
        dataset_id=dataset.id,
        decision=decision,
        accuracy=accuracy,
        true_positives=Measurement.count(
            "true_positives", tp, n, method_note=count_note
        ),
        true_negatives=Measurement.count(
            "true_negatives", tn, n, method_note=count_note
        ),
        false_positives=Measurement.count(
            "false_positives", fp, n, method_note=count_note
        ),
        false_negatives=Measurement.count(
            "false_negatives", fn, n, method_note=count_note
        ),
        items=results,
        started_at=started_at,
        finished_at=finished_at,
    )


def run_live(
    dataset: GoldenDataset,
    adapter: ModelAdapter,
    *,
    coverage_threshold: float = 0.8,
    max_unsupported_answer_rate: float = 0.2,
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> List[Evidence]:
    """Run the citation faithfulness probe against the dataset's questions."""
    probe = CitationFaithfulnessProbe(
        cases=[dataset.as_citation_case()],
        coverage_threshold=coverage_threshold,
        max_unsupported_answer_rate=max_unsupported_answer_rate,
        min_sample=min_sample,
    )
    return probe.run(adapter)
