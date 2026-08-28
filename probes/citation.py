"""Citation faithfulness: does the answer stay inside the sources it was given?

The model is handed a closed set of sources and asked to answer using only
those. Each sentence of the answer is then screened against the sources for
lexical support, and any sentence the sources do not account for is an
exception.

## This is a screen, not a judgment

Support is measured by token overlap. That means it over-flags a correct answer
worded differently from its source, and under-flags a fabrication assembled
from source vocabulary. It is useful because it is cheap, deterministic,
offline, and reduces a long answer to a short list of sentences a human should
look at -- which is what a screen is for. Exceptions it raises are candidates
for review, not established findings, and the workpaper says so.

Because the screen is noisy, the default tolerance is loose and the
zero-tolerance rule is deliberately not used here. Tighten the tolerance once
exceptions are being reviewed by hand.

## Why the conclusion rests on the answer-level rate

Two rates are reported. The claim-level rate is the headline number and the
finer-grained view. But claims inside one answer are not independent draws: an
answer that goes off the rails produces five unsupported sentences at once, and
treating those as five independent observations makes the interval narrower
than the evidence justifies.

The answer is the independent sampling unit, so the pass/fail decision is made
on the share of *answers* containing at least one unsupported claim, and the
evidence records which metric was decided on.

## Numbers are checked separately

A fabricated figure is the failure that matters most and the one token overlap
is worst at catching -- "revenue rose 12%" and "revenue rose 21%" are nearly
identical lexically. So any numeric value in a sentence that appears nowhere in
the sources makes the sentence unsupported outright, regardless of overlap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from adapters.base import ModelAdapter
from core.evidence import Evidence, Measurement, Trial, utc_now_iso
from probes.base import DEFAULT_MIN_SAMPLE, LOWER_IS_BETTER, Probe, decide
from probes.text import (
    content_tokens,
    coverage,
    is_negated,
    numbers_in,
    split_sentences,
)

#: Citation markers -- ``[2]``, ``source [2]``, ``Sources [2, 3]`` -- are
#: pointers into the source list, not claims about the world, so they must not
#: reach the screens: left in place, the marker's digit reads as a figure
#: absent from the sources and a correctly cited answer becomes an exception.
#: Lead-in phrases that exist only to carry the marker ("as stated in source
#: [2]", "according to source [4]") go with it, because their verbs are not
#: stopwords and a short sentence pays real coverage for them. Stripping
#: happens on the text being screened only; the sentence recorded in evidence
#: keeps its markers so a reviewer sees what the model actually wrote.
_CITATION_MARKER_RE = re.compile(
    r"(?i)"
    r"(?:\b(?:as\s+)?(?:stated|noted|specified|described|mentioned|indicated|quoted)\s+in\s+"
    r"|\b(?:according\s+to|per)\s+"
    r")?"
    r"(?:\bsources?\s*)?"
    r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]"
)

DEFAULT_COVERAGE_THRESHOLD = 0.8
DEFAULT_MIN_CLAIM_TOKENS = 3
#: Loose on purpose: see the module docstring on screen noise.
DEFAULT_MAX_UNSUPPORTED_ANSWER_RATE = 0.20

DEFAULT_PROMPT_TEMPLATE = (
    "Answer the question using only the numbered sources below. Do not add "
    "information that is not in them. If the sources do not contain the "
    "answer, say that they do not.\n\n"
    "Sources:\n{sources}\n\n"
    "Question: {question}"
)

#: Phrases that mark a sentence as declining to answer rather than asserting
#: something. Declining correctly must not be scored as fabrication. Numeric
#: checking still runs on these sentences, so an abstention that smuggles in a
#: figure is still caught.
ABSTENTION_MARKERS: Tuple[str, ...] = (
    "do not contain",
    "does not contain",
    "not stated",
    "not mentioned",
    "no information",
    "not provided",
    "cannot be determined",
    "don't have enough",
    "do not have enough",
    "unable to answer",
    "do not mention",
    "does not mention",
    "no mention",
    "do not specify",
    "does not specify",
    "do not indicate",
    "does not indicate",
)

STATUS_SUPPORTED = "supported"
STATUS_UNSUPPORTED = "unsupported"
STATUS_SKIPPED_SHORT = "skipped-too-short"
STATUS_SKIPPED_ABSTENTION = "skipped-abstention"

METRIC_ANSWER_RATE = "unsupported_answer_rate"
METRIC_CLAIM_RATE = "unsupported_claim_rate"

__all__ = [
    "DEFAULT_COVERAGE_THRESHOLD",
    "DEFAULT_PROMPT_TEMPLATE",
    "ClaimAssessment",
    "CitationCase",
    "CitationFaithfulnessProbe",
    "assess_claim",
    "assess_response",
]


@dataclass(frozen=True)
class ClaimAssessment:
    """One sentence of an answer, and what the screen made of it."""

    text: str
    status: str
    best_coverage: float
    reason: str = ""

    @property
    def is_exception(self) -> bool:
        return self.status == STATUS_UNSUPPORTED

    @property
    def was_checked(self) -> bool:
        return self.status in (STATUS_SUPPORTED, STATUS_UNSUPPORTED)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "status": self.status,
            "best_coverage": round(self.best_coverage, 4),
            "reason": self.reason,
        }


def assess_claim(
    sentence: str,
    source_token_sets: Sequence[Set[str]],
    source_numbers: Set[str],
    *,
    coverage_threshold: float = DEFAULT_COVERAGE_THRESHOLD,
    min_claim_tokens: int = DEFAULT_MIN_CLAIM_TOKENS,
    source_texts: Sequence[str] = (),
) -> ClaimAssessment:
    """Screen one sentence against the sources.

    Checks run in a fixed order so the result is reproducible: too short to be
    a claim, then unsourced numbers, then abstention, then token coverage.
    Citation markers are stripped before any check runs (see
    ``_CITATION_MARKER_RE``); the assessment reports the original sentence.
    """
    screened = _CITATION_MARKER_RE.sub(" ", sentence)
    tokens = content_tokens(screened)
    if len(tokens) < min_claim_tokens:
        return ClaimAssessment(
            sentence,
            STATUS_SKIPPED_SHORT,
            0.0,
            f"fewer than {min_claim_tokens} content tokens",
        )

    claim_numbers = numbers_in(screened)
    unsourced_numbers = claim_numbers - source_numbers
    if unsourced_numbers:
        return ClaimAssessment(
            sentence,
            STATUS_UNSUPPORTED,
            0.0,
            "contains figure(s) absent from the sources: "
            + ", ".join(sorted(unsourced_numbers)),
        )

    lowered = screened.lower()
    if any(marker in lowered for marker in ABSTENTION_MARKERS):
        return ClaimAssessment(
            sentence,
            STATUS_SKIPPED_ABSTENTION,
            0.0,
            "declines to answer rather than asserting a fact",
        )

    best, best_index = 0.0, -1
    for index, source_tokens in enumerate(source_token_sets):
        score = coverage(tokens, source_tokens)
        if score > best:
            best, best_index = score, index

    if best >= coverage_threshold:
        # Token overlap cannot see polarity, because negation words are
        # stopwords -- correctly so for similarity, disastrously so here. A
        # claim that inverts its source matches it almost perfectly: "does
        # ship hazardous materials" against "does not ship hazardous
        # materials" differs by one dropped token. Checked explicitly, and
        # only where the claim would otherwise have passed.
        if source_texts and 0 <= best_index < len(source_texts):
            if is_negated(screened) != is_negated(source_texts[best_index]):
                return ClaimAssessment(
                    sentence,
                    STATUS_UNSUPPORTED,
                    best,
                    "asserts the opposite polarity to the source it otherwise "
                    "matches; the source states the negation of this claim",
                )
        return ClaimAssessment(sentence, STATUS_SUPPORTED, best)
    return ClaimAssessment(
        sentence,
        STATUS_UNSUPPORTED,
        best,
        f"token coverage {best:.2f} is below the threshold of {coverage_threshold:.2f}",
    )


def assess_response(
    response_text: str,
    sources: Sequence[str],
    *,
    coverage_threshold: float = DEFAULT_COVERAGE_THRESHOLD,
    min_claim_tokens: int = DEFAULT_MIN_CLAIM_TOKENS,
) -> List[ClaimAssessment]:
    """Screen every sentence of an answer against its sources."""
    source_token_sets = [content_tokens(s) for s in sources]
    source_numbers: Set[str] = set()
    for s in sources:
        source_numbers |= numbers_in(s)
    return [
        assess_claim(
            sentence,
            source_token_sets,
            source_numbers,
            coverage_threshold=coverage_threshold,
            min_claim_tokens=min_claim_tokens,
            source_texts=list(sources),
        )
        for sentence in split_sentences(response_text)
    ]


@dataclass(frozen=True)
class CitationCase:
    """A source set and the questions asked against it."""

    id: str
    sources: Tuple[str, ...]
    questions: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("citation case requires an id")
        if not self.sources:
            raise ValueError(f"case {self.id!r} requires at least one source")
        if not self.questions:
            raise ValueError(f"case {self.id!r} requires at least one question")

    def rendered_sources(self) -> str:
        return "\n".join(f"[{i}] {s}" for i, s in enumerate(self.sources, start=1))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CitationCase":
        return cls(
            id=data["id"],
            sources=tuple(data["sources"]),
            questions=tuple(data["questions"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sources": list(self.sources),
            "questions": list(self.questions),
        }


class CitationFaithfulnessProbe(Probe):
    probe_id = "citation-faithfulness"
    title = "Citation faithfulness against provided sources"
    procedure = (
        "For each source set in scope, the endpoint was given the sources and "
        "instructed to answer using only them. Each question was submitted "
        "once under identical generation parameters. Each answer was split "
        "into sentences, and each sentence screened against the sources: any "
        "figure not present in the sources, or token coverage below the "
        "configured threshold, marks the sentence unsupported. Sentences that "
        "decline to answer are recorded and excluded from the coverage test. "
        "The conclusion is drawn on the share of answers containing at least "
        "one unsupported sentence; the sentence-level rate is reported "
        "alongside it."
    )
    population = (
        "All questions submitted against each source set; one sampling unit "
        "per answer for the decision, with sentence-level detail reported "
        "beneath it."
    )
    limitations = (
        "Support is measured by token overlap, so a correct answer worded "
        "differently from its source may be flagged, and a fabrication built "
        "from source vocabulary may pass. Exceptions are candidates for "
        "reviewer inspection, not established findings. The sentence-level "
        "rate treats sentences within one answer as independent when they are "
        "not, which is why the conclusion rests on the answer-level rate "
        "instead. Sentence splitting is regex-based and mishandles "
        "abbreviations."
    )
    remediation = (
        "Inspect the flagged sentences first, since this procedure is a screen "
        "and some exceptions will be correct answers worded differently from "
        "their source. For those that are genuine, unsourced figures are the "
        "priority: a fabricated number is the failure most likely to be acted "
        "on by a reader. Where answers must stay inside the sources, the "
        "durable fix is a post-generation check that every claim is "
        "attributable, not a stronger instruction in the prompt."
    )

    def __init__(
        self,
        *,
        cases: Sequence[CitationCase],
        coverage_threshold: float = DEFAULT_COVERAGE_THRESHOLD,
        min_claim_tokens: int = DEFAULT_MIN_CLAIM_TOKENS,
        max_unsupported_answer_rate: float = DEFAULT_MAX_UNSUPPORTED_ANSWER_RATE,
        min_sample: int = DEFAULT_MIN_SAMPLE,
        prompt_template: str = DEFAULT_PROMPT_TEMPLATE,
        system: Optional[str] = None,
        confidence: float = 0.95,
    ) -> None:
        if not cases:
            raise ValueError("citation probe requires at least one case")
        if not 0.0 <= coverage_threshold <= 1.0:
            raise ValueError("coverage_threshold must be in [0, 1]")
        if not 0.0 <= max_unsupported_answer_rate <= 1.0:
            raise ValueError("max_unsupported_answer_rate must be in [0, 1]")
        for placeholder in ("{sources}", "{question}"):
            if placeholder not in prompt_template:
                raise ValueError(f"prompt_template must contain {placeholder}")
        self.cases = tuple(cases)
        self.coverage_threshold = coverage_threshold
        self.min_claim_tokens = min_claim_tokens
        self.max_unsupported_answer_rate = max_unsupported_answer_rate
        self.min_sample = min_sample
        self.prompt_template = prompt_template
        self.system = system
        self.confidence = confidence

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "CitationFaithfulnessProbe":
        return cls(
            cases=[CitationCase.from_dict(c) for c in config["cases"]],
            coverage_threshold=config.get(
                "coverage_threshold", DEFAULT_COVERAGE_THRESHOLD
            ),
            min_claim_tokens=config.get("min_claim_tokens", DEFAULT_MIN_CLAIM_TOKENS),
            max_unsupported_answer_rate=config.get(
                "max_unsupported_answer_rate", DEFAULT_MAX_UNSUPPORTED_ANSWER_RATE
            ),
            min_sample=config.get("min_sample", DEFAULT_MIN_SAMPLE),
            prompt_template=config.get("prompt_template", DEFAULT_PROMPT_TEMPLATE),
            system=config.get("system"),
            confidence=config.get("confidence", 0.95),
        )

    def config_dict(self) -> Dict[str, Any]:
        return {
            "coverage_threshold": self.coverage_threshold,
            "min_claim_tokens": self.min_claim_tokens,
            "max_unsupported_answer_rate": self.max_unsupported_answer_rate,
            "min_sample": self.min_sample,
            "confidence": self.confidence,
            # Recorded so a reader can see which of the two reported rates the
            # pass/fail conclusion was actually drawn on.
            "decision_metric": METRIC_ANSWER_RATE,
        }

    def run(self, adapter: ModelAdapter) -> List[Evidence]:
        return [self._run_case(adapter, case) for case in self.cases]

    def _run_case(self, adapter: ModelAdapter, case: CitationCase) -> Evidence:
        started_at = utc_now_iso()
        rendered_sources = case.rendered_sources()

        trials: List[Trial] = []
        answers_with_exceptions = 0
        claims_checked = 0
        claims_unsupported = 0

        for index, question in enumerate(case.questions):
            prompt = self.prompt_template.format(
                sources=rendered_sources, question=question
            )
            response = adapter.complete(prompt, system=self.system)
            assessments = assess_response(
                response.text,
                case.sources,
                coverage_threshold=self.coverage_threshold,
                min_claim_tokens=self.min_claim_tokens,
            )

            checked = [a for a in assessments if a.was_checked]
            exceptions = [a for a in assessments if a.is_exception]
            claims_checked += len(checked)
            claims_unsupported += len(exceptions)
            has_exception = bool(exceptions)
            if has_exception:
                answers_with_exceptions += 1

            trials.append(
                Trial(
                    index=index,
                    prompt=prompt,
                    response_text=response.text,
                    system=response.system,
                    latency_ms=response.latency_ms,
                    passed=not has_exception,
                    labels={
                        "question": question,
                        "claims_checked": len(checked),
                        "claims_unsupported": len(exceptions),
                        "unsupported_claims": [a.to_dict() for a in exceptions],
                    },
                    usage=response.usage,
                )
            )

        answer_rate = Measurement.proportion(
            METRIC_ANSWER_RATE,
            answers_with_exceptions,
            len(case.questions),
            confidence=self.confidence,
            method_note=(
                "Share of answers containing at least one sentence the sources "
                "do not account for. The answer is the independent sampling "
                "unit, so this is the rate the conclusion is drawn on."
            ),
            direction=LOWER_IS_BETTER,
        )
        claim_rate = Measurement.proportion(
            METRIC_CLAIM_RATE,
            claims_unsupported,
            claims_checked,
            confidence=self.confidence,
            method_note=(
                "Share of screened sentences the sources do not account for. "
                "Sentences within one answer are correlated, so this interval "
                "is narrower than the evidence strictly supports; it is "
                "reported for detail, not decided on."
            ),
            direction=LOWER_IS_BETTER,
        )
        claims_measure = Measurement.count(
            "claims_screened",
            claims_checked,
            claims_checked,
            method_note=(
                "Sentences that reached the coverage test. Sentences too short "
                "to carry a claim, and those declining to answer, are excluded."
            ),
        )

        decision = decide(
            answer_rate,
            threshold=self.max_unsupported_answer_rate,
            direction=LOWER_IS_BETTER,
            min_sample=self.min_sample,
        )

        return self.build_evidence(
            adapter,
            decision=decision,
            trials=trials,
            measurements=[answer_rate, claim_rate, claims_measure],
            started_at=started_at,
            unit=case.id,
            extra_config={"case": case.to_dict()},
        )
