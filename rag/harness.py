"""Run the golden RAG dataset against the citation screen (offline or live).

Screen-check mode never calls a model: it scores each ``gold_answer`` with
``assess_response`` and compares the result to the planted ``expect`` label.
Live mode builds a ``CitationCase`` and runs ``CitationFaithfulnessProbe``.

## Why the headline number is stratified

An overall accuracy figure over a golden dataset is a weighted average across
whatever mix of cases the author chose to write. That makes it movable at will:
adding ten more near-verbatim items raises it without the screen getting any
better. An earlier version of this dataset reported 100% accuracy while the
screen was wrong on every realistic paraphrase and entity swap put to it -- the
figure was measuring the dataset, not the method.

So accuracy is reported per category, each with its own interval and sample
size, and the conclusion is rolled up by precedence exactly as a battery is
(D-016): fail if any category fails, inconclusive if any is inconclusive, pass
only if every one passes. A category the screen cannot do drags the verdict
down instead of being averaged away, which is the whole point of keeping those
items in the dataset.

The overall figure is still reported, labelled as composition-dependent, so a
reader who wants one number has one -- with the warning attached.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from adapters.base import ModelAdapter
from core.evidence import (
    DIRECTION_HIGHER_IS_BETTER,
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
    Evidence,
    Measurement,
    utc_now_iso,
)
from probes.base import HIGHER_IS_BETTER, RULE_INTERVAL, Decision, decide
from probes.citation import (
    CitationFaithfulnessProbe,
    assess_response,
)
from rag.dataset import (
    CATEGORY_UNSPECIFIED,
    EXPECT_FAITHFUL,
    EXPECT_UNFAITHFUL,
    GoldenDataset,
    GoldenItem,
)

#: Minimum share of planted labels the screen must get right to pass.
DEFAULT_MIN_SCREEN_ACCURACY = 0.9
DEFAULT_MIN_SAMPLE = 20
#: Categories are smaller than the dataset by construction, so requiring 20 per
#: stratum would make every category inconclusive and the check useless. Eight
#: is enough for a category the screen cannot do at all to fail decisively --
#: 0/8 puts the interval's upper bound near 0.32, well under any sane
#: threshold -- while still refusing to certify a category from a handful of
#: easy examples.
DEFAULT_STRATUM_MIN_SAMPLE = 8

METRIC_SCREEN_ACCURACY = "screen_accuracy"

__all__ = [
    "DEFAULT_MIN_SCREEN_ACCURACY",
    "DEFAULT_MIN_SAMPLE",
    "DEFAULT_STRATUM_MIN_SAMPLE",
    "METRIC_SCREEN_ACCURACY",
    "ItemScreenResult",
    "StratumResult",
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
    category: str = CATEGORY_UNSPECIFIED
    #: The dataset predicted the screen would get this wrong.
    known_screen_miss: bool = False

    @property
    def surprising(self) -> bool:
        """Wrong in a way the dataset did not anticipate.

        A documented blind spot failing is expected. An item failing that
        nobody flagged is new information, and worth surfacing separately.
        """
        return not self.correct and not self.known_screen_miss

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "category": self.category,
            "expect": self.expect,
            "flagged_unfaithful": self.flagged_unfaithful,
            "correct": self.correct,
            "known_screen_miss": self.known_screen_miss,
            "unsupported_count": self.unsupported_count,
        }


@dataclass(frozen=True)
class StratumResult:
    """Accuracy within one category of case."""

    category: str
    accuracy: Measurement
    decision: Decision
    items: Tuple[ItemScreenResult, ...]

    @property
    def outcome(self) -> str:
        return self.decision.outcome

    @property
    def misses(self) -> Tuple[ItemScreenResult, ...]:
        return tuple(i for i in self.items if not i.correct)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "outcome": self.outcome,
            "accuracy": self.accuracy.to_dict(),
            "rationale": self.decision.rationale,
            "items": [i.to_dict() for i in self.items],
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
    strata: Tuple[StratumResult, ...] = ()
    #: The probe this check grades, by its registered id. The dataset does not
    #: carry the probe's name, so without this field the result and the probe
    #: it grades share no identifier at all.
    probe_id: str = CitationFaithfulnessProbe.probe_id
    #: sha256 of the dataset file this was computed against.
    #:
    #: `dataset_id` names which dataset; this names which VERSION of it. When
    #: the golden set went from 56 items to 128 the accuracy moved from 0.571 to
    #: 0.625 and every stratum changed, while `dataset_id` stayed put — so a
    #: consumer holding this result could not tell which input produced it, and
    #: neither could a later reader of the repository. Empty where the dataset
    #: was built in memory rather than read from a file.
    dataset_sha256: str = ""

    @property
    def outcome(self) -> str:
        return self.decision.outcome

    @property
    def failing_categories(self) -> Tuple[str, ...]:
        return tuple(s.category for s in self.strata if s.outcome == OUTCOME_FAIL)

    @property
    def surprises(self) -> Tuple[ItemScreenResult, ...]:
        """Misses the dataset did not predict -- new information."""
        return tuple(i for i in self.items if i.surprising)

    def summary_lines(self) -> List[str]:
        lines = [
            f"rag screen-check [{self.outcome.upper()}] {self.dataset_id}",
            f"  {self.decision.rationale}",
            "",
            f"  overall {self.accuracy.render()}"
            "  <- depends on the mix of cases in the dataset, not on the "
            "screen alone",
            (
                f"  tp={int(self.true_positives.value)}  "
                f"tn={int(self.true_negatives.value)}  "
                f"fp={int(self.false_positives.value)}  "
                f"fn={int(self.false_negatives.value)}  "
                f"(n={self.accuracy.n})"
            ),
        ]
        if self.strata:
            width = max(len(s.category) for s in self.strata)
            lines.append("")
            lines.append("  by category:")
            for stratum in self.strata:
                lines.append(
                    f"    [{stratum.outcome.upper():>12}] "
                    f"{stratum.category:<{width}}  {stratum.accuracy.render()}"
                )
        if self.failing_categories:
            lines.append("")
            lines.append(
                "  The screen is not usable on: "
                + ", ".join(self.failing_categories)
                + ". These are kept in the dataset deliberately; deleting them "
                "would raise the overall figure and change nothing real."
            )
        if self.surprises:
            lines.append("")
            lines.append(
                "  Unanticipated misses (not flagged as known blind spots):"
            )
            for miss in self.surprises:
                lines.append(
                    f"    {miss.item_id} [{miss.category}]: expect={miss.expect} "
                    f"flagged_unfaithful={miss.flagged_unfaithful}"
                )
        return lines

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_sha256": self.dataset_sha256,
            "probe_id": self.probe_id,
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
            "strata": [s.to_dict() for s in self.strata],
            "failing_categories": list(self.failing_categories),
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
        category=item.category,
        known_screen_miss=item.known_screen_miss,
    )


def _roll_up(
    strata: Sequence[StratumResult],
    min_accuracy: float,
    min_sample: int,
    overall: Measurement,
) -> Decision:
    """Combine per-category decisions by precedence, as a battery does.

    Falls back to deciding on the aggregate only when the dataset carries no
    categories at all -- an unstratified dataset cannot say where the screen
    breaks, and the rationale says so.
    """
    if not strata:
        decision = decide(
            overall,
            threshold=min_accuracy,
            direction=HIGHER_IS_BETTER,
            min_sample=min_sample,
        )
        return Decision(
            decision.outcome,
            decision.rationale
            + " This dataset carries no category labels, so the figure is an "
            "average over an unknown mix of cases and cannot show which kinds "
            "of claim the screen handles.",
            decision.rule,
            decision.threshold,
            decision.direction,
        )

    failing = [s for s in strata if s.outcome == OUTCOME_FAIL]
    inconclusive = [s for s in strata if s.outcome == OUTCOME_INCONCLUSIVE]
    if failing:
        names = ", ".join(s.category for s in failing)
        return Decision(
            OUTCOME_FAIL,
            f"The screen fails on {len(failing)} of {len(strata)} categories "
            f"({names}). Judged per category rather than on the aggregate, "
            "which would average a category the screen cannot do into ones it "
            "can.",
            RULE_INTERVAL,
            min_accuracy,
            HIGHER_IS_BETTER,
        )
    if inconclusive:
        names = ", ".join(s.category for s in inconclusive)
        return Decision(
            OUTCOME_INCONCLUSIVE,
            f"No category failed, but {len(inconclusive)} of {len(strata)} "
            f"({names}) have too few items to conclude the screen handles "
            "them. More items in those categories, not more items overall.",
            RULE_INTERVAL,
            min_accuracy,
            HIGHER_IS_BETTER,
        )
    return Decision(
        OUTCOME_PASS,
        f"Every one of the {len(strata)} categories met the required accuracy "
        f"of {min_accuracy:.3f} with an adequate sample.",
        RULE_INTERVAL,
        min_accuracy,
        HIGHER_IS_BETTER,
    )


def run_screen_check(
    dataset: GoldenDataset,
    *,
    min_accuracy: float = DEFAULT_MIN_SCREEN_ACCURACY,
    min_sample: int = DEFAULT_MIN_SAMPLE,
    stratum_min_sample: int = DEFAULT_STRATUM_MIN_SAMPLE,
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
    # Per-category accuracy, then a precedence rollup. The aggregate above is
    # reported but deliberately not decided on: it moves with the dataset's
    # composition, and a category the screen cannot handle would disappear
    # into it.
    strata: List[StratumResult] = []
    for category in dataset.categories:
        in_category = tuple(r for r in results if r.category == category)
        if not in_category:  # pragma: no cover - categories come from items
            continue
        stratum_accuracy = Measurement.proportion(
            METRIC_SCREEN_ACCURACY,
            sum(1 for r in in_category if r.correct),
            len(in_category),
            confidence=confidence,
            method_note=(
                f"Screen accuracy restricted to {category!r} items. Unlike the "
                "overall figure, this cannot be raised by adding easier cases "
                "of a different kind."
            ),
            direction=DIRECTION_HIGHER_IS_BETTER,
        )
        strata.append(
            StratumResult(
                category=category,
                accuracy=stratum_accuracy,
                decision=decide(
                    stratum_accuracy,
                    threshold=min_accuracy,
                    direction=HIGHER_IS_BETTER,
                    min_sample=stratum_min_sample,
                ),
                items=in_category,
            )
        )

    decision = _roll_up(strata, min_accuracy, min_sample, accuracy)
    count_note = (
        "Confusion counts treating 'unfaithful' as the positive class. "
        "Reported as tallies, not combined into a composite score."
    )
    finished_at = utc_now_iso()
    return ScreenCheckResult(
        dataset_id=dataset.id,
        dataset_sha256=dataset.content_sha256,
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
        strata=tuple(strata),
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
