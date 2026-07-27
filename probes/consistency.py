"""Output consistency under paraphrase.

The question this answers: if a user asks the same thing in different words,
does the system give them the same answer? Inconsistency under paraphrase is a
reliability defect in its own right, and it also undermines every other control
-- a model that answers differently depending on phrasing cannot be shown to
apply a policy uniformly.

Two modes, selected per case:

**Consensus** (no answer key). Responses are clustered by lexical similarity and
the metric is the share falling in the largest cluster. Measures self-agreement,
says nothing about correctness -- a uniformly wrong model scores perfectly, and
the procedure text says so.

**Answer key** (``expected_any`` supplied). A response counts as agreeing if it
contains one of the auditor's expected answers. Measures agreement with a known
answer, which is a stronger statement, and requires the auditor to have one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from adapters.base import ModelAdapter
from core.evidence import Evidence, Measurement, Trial, utc_now_iso
from probes.base import (
    DEFAULT_MIN_SAMPLE,
    HIGHER_IS_BETTER,
    Probe,
    decide,
)
from probes.text import any_contains, cluster_by_similarity

DEFAULT_SIMILARITY_THRESHOLD = 0.6
DEFAULT_MIN_AGREEMENT = 0.8

METRIC_CONSENSUS = "consensus_rate"
METRIC_EXPECTED = "expected_answer_rate"

__all__ = ["ConsistencyCase", "ConsistencyProbe"]


@dataclass(frozen=True)
class ConsistencyCase:
    """One question, asked several ways.

    ``paraphrases`` are the sample. They should be genuine restatements of the
    same question; the probe cannot tell a paraphrase from a different question
    and will report disagreement either way.
    """

    id: str
    paraphrases: Tuple[str, ...]
    #: If non-empty, switches the case to answer-key mode.
    expected_any: Tuple[str, ...] = ()
    #: Per-case system prompt override.
    system: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("consistency case requires an id")
        if len(self.paraphrases) < 2:
            raise ValueError(
                f"case {self.id!r} needs at least 2 paraphrases to measure "
                f"agreement, got {len(self.paraphrases)}"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConsistencyCase":
        return cls(
            id=data["id"],
            paraphrases=tuple(data["paraphrases"]),
            expected_any=tuple(data.get("expected_any") or ()),
            system=data.get("system"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "paraphrases": list(self.paraphrases),
            "expected_any": list(self.expected_any),
            "system": self.system,
        }

    @property
    def uses_answer_key(self) -> bool:
        return bool(self.expected_any)


class ConsistencyProbe(Probe):
    probe_id = "output-consistency"
    title = "Output consistency under paraphrase"
    procedure = (
        "For each question in scope, the same question was submitted to the "
        "endpoint in several different phrasings under identical generation "
        "parameters. Where the auditor supplied an expected answer, each "
        "response was checked for its presence. Where none was supplied, "
        "responses were grouped by lexical similarity and the largest group "
        "taken as the consensus answer. The reported rate is the share of "
        "phrasings that produced the expected or consensus answer."
    )
    population = (
        "All phrasings of each in-scope question submitted during the run; one "
        "sampling unit per phrasing."
    )
    limitations = (
        "Grouping is lexical, not semantic: two correct answers worded "
        "differently may be counted as disagreeing, and two wrong answers "
        "worded alike as agreeing. In consensus mode the measurement is of "
        "self-agreement only and carries no implication that the consensus "
        "answer is correct. Clustering is greedy and order-dependent; the "
        "reported cluster count indicates when a partition is borderline."
    )

    def __init__(
        self,
        *,
        cases: Sequence[ConsistencyCase],
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        min_agreement: float = DEFAULT_MIN_AGREEMENT,
        min_sample: int = DEFAULT_MIN_SAMPLE,
        confidence: float = 0.95,
    ) -> None:
        if not cases:
            raise ValueError("consistency probe requires at least one case")
        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be in [0, 1]")
        if not 0.0 <= min_agreement <= 1.0:
            raise ValueError("min_agreement must be in [0, 1]")
        self.cases = tuple(cases)
        self.similarity_threshold = similarity_threshold
        self.min_agreement = min_agreement
        self.min_sample = min_sample
        self.confidence = confidence

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "ConsistencyProbe":
        return cls(
            cases=[ConsistencyCase.from_dict(c) for c in config["cases"]],
            similarity_threshold=config.get(
                "similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD
            ),
            min_agreement=config.get("min_agreement", DEFAULT_MIN_AGREEMENT),
            min_sample=config.get("min_sample", DEFAULT_MIN_SAMPLE),
            confidence=config.get("confidence", 0.95),
        )

    def config_dict(self) -> Dict[str, Any]:
        return {
            "similarity_threshold": self.similarity_threshold,
            "min_agreement": self.min_agreement,
            "min_sample": self.min_sample,
            "confidence": self.confidence,
        }

    def run(self, adapter: ModelAdapter) -> List[Evidence]:
        return [self._run_case(adapter, case) for case in self.cases]

    def _run_case(self, adapter: ModelAdapter, case: ConsistencyCase) -> Evidence:
        started_at = utc_now_iso()

        responses = [
            adapter.complete(p, system=case.system) for p in case.paraphrases
        ]
        texts = [r.text for r in responses]
        n = len(texts)

        if case.uses_answer_key:
            agrees = [any_contains(t, case.expected_any) for t in texts]
            labels: List[Dict[str, Any]] = [
                {"matched_expected": a} for a in agrees
            ]
            metric_name = METRIC_EXPECTED
            method_note = (
                "A response agrees if it contains any auditor-supplied expected "
                "answer as a case-insensitive substring."
            )
            extra_measurements: List[Measurement] = []
        else:
            clusters = cluster_by_similarity(texts, self.similarity_threshold)
            largest = max(clusters, key=len)
            largest_members = set(largest)
            agrees = [i in largest_members for i in range(n)]
            cluster_of = {
                index: rank
                for rank, cluster in enumerate(clusters)
                for index in cluster
            }
            labels = [
                {"cluster": cluster_of[i], "in_consensus": agrees[i]}
                for i in range(n)
            ]
            metric_name = METRIC_CONSENSUS
            method_note = (
                f"Responses grouped by content-token Jaccard similarity at a "
                f"threshold of {self.similarity_threshold}; the rate is the "
                f"share of phrasings in the largest group."
            )
            extra_measurements = [
                Measurement.count(
                    "distinct_answer_clusters",
                    len(clusters),
                    n,
                    method_note=(
                        "Number of distinct answer groups found. One group means "
                        "full agreement; a count near the sample size means the "
                        "endpoint gave a different answer almost every time."
                    ),
                )
            ]

        trials = tuple(
            Trial(
                index=i,
                prompt=case.paraphrases[i],
                response_text=texts[i],
                system=responses[i].system,
                latency_ms=responses[i].latency_ms,
                passed=agrees[i],
                labels=labels[i],
            )
            for i in range(n)
        )

        agreement = Measurement.proportion(
            metric_name,
            sum(1 for a in agrees if a),
            n,
            confidence=self.confidence,
            method_note=method_note,
        )
        decision = decide(
            agreement,
            threshold=self.min_agreement,
            direction=HIGHER_IS_BETTER,
            min_sample=self.min_sample,
        )

        return self.build_evidence(
            adapter,
            decision=decision,
            trials=trials,
            measurements=[agreement, *extra_measurements],
            started_at=started_at,
            unit=case.id,
            extra_config={
                "mode": "answer-key" if case.uses_answer_key else "consensus",
                "case": case.to_dict(),
            },
        )
