"""Deterministic, scriptable, offline model.

This is the substrate the whole test suite runs on (D-001). Two properties
matter and both are load-bearing:

**Deterministic.** Given the same script and the same sequence of calls, a
fresh adapter returns byte-identical text *and* identical reported latency.
Latency is derived from a hash of the call, not the clock, so evidence records
hash identically across runs and machines -- which is what lets Phase 2 test
the journal's hash chain against fixtures at all.

**Scriptable.** Probe tests need a model that passes and a model that fails,
including partial failures ("leaks the canary on the 4th of 10 attempts"), so
rules match on prompt or system text and cycle through a response sequence.

The fallback response for an unmatched prompt is a neutral, visibly-mock echo.
It is deliberately not a refusal and not an agreement: a default that leaned
either way would quietly decide the result of any probe whose script had a gap,
and that bug would look like a finding.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from adapters.base import AdapterError, ModelAdapter, ModelResponse

MATCH_SUBSTRING = "substring"
MATCH_EXACT = "exact"
MATCH_REGEX = "regex"
MATCH_ANY = "any"
MATCH_MODES = frozenset({MATCH_SUBSTRING, MATCH_EXACT, MATCH_REGEX, MATCH_ANY})

TARGET_PROMPT = "prompt"
TARGET_SYSTEM = "system"
TARGET_BOTH = "both"
TARGETS = frozenset({TARGET_PROMPT, TARGET_SYSTEM, TARGET_BOTH})

DEFAULT_MODEL = "mock-deterministic-v1"

ResponseSpec = Union[str, Sequence[str]]

__all__ = [
    "load_mock_script",
    "MATCH_SUBSTRING",
    "MATCH_EXACT",
    "MATCH_REGEX",
    "MATCH_ANY",
    "TARGET_PROMPT",
    "TARGET_SYSTEM",
    "TARGET_BOTH",
    "DEFAULT_MODEL",
    "MockRule",
    "MockAdapter",
]


@dataclass(frozen=True)
class MockRule:
    """One scripted behaviour: what to match, and what to answer.

    Rules are evaluated in order and the first match wins, so put specific
    rules before general ones.
    """

    responses: Tuple[str, ...] = ()
    pattern: str = ""
    mode: str = MATCH_SUBSTRING
    target: str = TARGET_PROMPT
    case_sensitive: bool = False
    #: If set, matching this rule raises ``AdapterError`` with this message
    #: instead of returning text -- for exercising probe error handling.
    error: Optional[str] = None
    #: When responses run out: True re-cycles from the start, False raises.
    #: Set False when a test asserts the exact number of calls a probe makes.
    cycle: bool = True

    def __post_init__(self) -> None:
        if self.mode not in MATCH_MODES:
            raise ValueError(
                f"unknown match mode {self.mode!r}; expected one of {sorted(MATCH_MODES)}"
            )
        if self.target not in TARGETS:
            raise ValueError(
                f"unknown target {self.target!r}; expected one of {sorted(TARGETS)}"
            )
        if not self.responses and self.error is None:
            raise ValueError("a rule must supply responses or an error")
        if self.responses and self.error is not None:
            raise ValueError("a rule supplies responses or an error, not both")
        if self.mode != MATCH_ANY and not self.pattern:
            raise ValueError(f"match mode {self.mode!r} requires a pattern")
        if self.mode == MATCH_REGEX:
            re.compile(self.pattern)  # fail loudly at construction, not mid-run

    @classmethod
    def make(
        cls,
        pattern: str = "",
        responses: ResponseSpec = (),
        **kwargs: Any,
    ) -> "MockRule":
        """Constructor that accepts a bare string for ``responses``."""
        if isinstance(responses, str):
            responses = (responses,)
        return cls(responses=tuple(responses), pattern=pattern, **kwargs)

    def _haystacks(self, prompt: str, system: Optional[str]) -> List[str]:
        if self.target == TARGET_PROMPT:
            return [prompt]
        if self.target == TARGET_SYSTEM:
            return [system or ""]
        return [prompt, system or ""]

    def matches(self, prompt: str, system: Optional[str]) -> bool:
        if self.mode == MATCH_ANY:
            return True
        for haystack in self._haystacks(prompt, system):
            hay = haystack if self.case_sensitive else haystack.lower()
            needle = self.pattern if self.case_sensitive else self.pattern.lower()
            if self.mode == MATCH_SUBSTRING and needle in hay:
                return True
            if self.mode == MATCH_EXACT and hay == needle:
                return True
            if self.mode == MATCH_REGEX:
                flags = 0 if self.case_sensitive else re.IGNORECASE
                if re.search(self.pattern, haystack, flags):
                    return True
        return False


class MockAdapter(ModelAdapter):
    """Offline model whose every answer is either scripted or hash-derived."""

    name = "mock"
    requires_network = False

    def __init__(
        self,
        rules: Sequence[MockRule] = (),
        *,
        model: str = DEFAULT_MODEL,
        params: Optional[Dict[str, Any]] = None,
        system: Optional[str] = None,
        default_response: Optional[str] = None,
        seed: int = 0,
    ) -> None:
        merged_params = dict(params or {})
        # Seed participates in the fingerprint: two mocks that answer
        # differently must not claim to be the same configuration.
        merged_params.setdefault("seed", seed)
        super().__init__(model=model, params=merged_params, system=system)
        self.rules: Tuple[MockRule, ...] = tuple(rules)
        self.default_response = default_response
        self.seed = seed
        #: Every call made, in order -- assertions read this.
        self.calls: List[Dict[str, Any]] = []
        self._hits: Dict[int, int] = {}

    # -- alternate constructors ----------------------------------------------

    @classmethod
    def always(cls, text: str, **kwargs: Any) -> "MockAdapter":
        """Answer every prompt with the same text."""
        return cls([MockRule.make(responses=(text,), mode=MATCH_ANY)], **kwargs)

    @classmethod
    def sequence(
        cls, responses: Sequence[str], *, cycle: bool = True, **kwargs: Any
    ) -> "MockAdapter":
        """Answer calls with ``responses`` in order, regardless of prompt.

        The workhorse for probe tests: it makes "3 of these 10 trials fail" a
        one-line fixture.
        """
        return cls(
            [MockRule.make(responses=tuple(responses), mode=MATCH_ANY, cycle=cycle)],
            **kwargs,
        )

    @classmethod
    def script(cls, mapping: Mapping[str, ResponseSpec], **kwargs: Any) -> "MockAdapter":
        """Build substring rules from an ordered mapping of pattern -> answer."""
        rules = [MockRule.make(pattern=p, responses=r) for p, r in mapping.items()]
        return cls(rules, **kwargs)

    # -- state ----------------------------------------------------------------

    def reset(self) -> None:
        """Clear the call log and rule counters, restoring first-call state."""
        self.calls.clear()
        self._hits.clear()

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def prompts_seen(self) -> List[str]:
        return [c["prompt"] for c in self.calls]

    # -- determinism helpers --------------------------------------------------

    def _digest(self, prompt: str, system: Optional[str]) -> str:
        material = "\x00".join(
            [self.model, str(self.seed), system or "", prompt]
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def _latency_for(self, digest: str) -> float:
        """Plausible but fully determined latency, in milliseconds.

        Derived from the call digest rather than measured, so that evidence
        records are reproducible byte-for-byte.
        """
        return 10.0 + (int(digest[:4], 16) % 2000) / 10.0

    def _fallback_text(self, prompt: str, digest: str) -> str:
        if self.default_response is not None:
            return self.default_response
        excerpt = prompt.strip().replace("\n", " ")[:120]
        return f"[mock:{digest[:12]}] response to: {excerpt}"

    # -- the interface --------------------------------------------------------

    def complete(self, prompt: str, *, system: Optional[str] = None) -> ModelResponse:
        effective_system = self.effective_system(system)
        digest = self._digest(prompt, effective_system)

        matched_index: Optional[int] = None
        text: Optional[str] = None

        for index, rule in enumerate(self.rules):
            if not rule.matches(prompt, effective_system):
                continue
            matched_index = index
            hit = self._hits.get(index, 0)
            self._hits[index] = hit + 1

            if rule.error is not None:
                self.calls.append(
                    {
                        "prompt": prompt,
                        "system": effective_system,
                        "rule": index,
                        "error": rule.error,
                    }
                )
                raise AdapterError(rule.error)

            if hit >= len(rule.responses) and not rule.cycle:
                raise AdapterError(
                    f"mock rule {index} exhausted after {len(rule.responses)} "
                    f"response(s); call {hit + 1} has no scripted answer"
                )
            text = rule.responses[hit % len(rule.responses)]
            break

        if text is None:
            text = self._fallback_text(prompt, digest)

        self.calls.append(
            {
                "prompt": prompt,
                "system": effective_system,
                "rule": matched_index,
                "text": text,
            }
        )

        return ModelResponse(
            text=text,
            model=self.model,
            latency_ms=self._latency_for(digest),
            system=effective_system,
            finish_reason="stop",
            usage={
                "prompt_tokens": len(prompt.split()),
                "completion_tokens": len(text.split()),
            },
            request_id=f"mock-{digest[:16]}",
            raw={"scripted": matched_index is not None},
        )


def load_mock_script(path: Any) -> MockAdapter:
    """Build a mock endpoint from a JSON fixture file.

    This exists so the toolkit can be demonstrated, and its own behaviour
    regression-tested, against an endpoint with *known* characteristics. Run
    the default suite against a bare mock and two probes fail -- not because
    anything is wrong, but because a hash-echo does not know a fictional
    shipping policy. Failures that are artifacts of the fixture are the worst
    possible first impression for an assurance tool, so fixtures are a
    first-class input.

    Format::

        {
          "model": "vendor-assistant-v2",
          "default_response": "...",         # optional
          "rules": [
            {"pattern": "base64", "responses": "here it is: SECRET"},
            {"pattern": "capital", "responses": ["Paris.", "Lyon."]},
            {"mode": "any", "responses": "fallback answer"}
          ]
        }
    """
    import json
    from pathlib import Path

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rules: List[MockRule] = []
    for index, entry in enumerate(data.get("rules", [])):
        if not isinstance(entry, dict):
            raise ValueError(f"mock script rule {index} must be an object")
        unknown = set(entry) - {
            "pattern",
            "responses",
            "mode",
            "target",
            "case_sensitive",
            "cycle",
            "error",
            "comment",
        }
        if unknown:
            raise ValueError(
                f"mock script rule {index} has unknown key(s): {sorted(unknown)}"
            )
        rules.append(
            MockRule.make(
                pattern=entry.get("pattern", ""),
                responses=entry.get("responses", ()),
                mode=entry.get("mode", MATCH_SUBSTRING),
                target=entry.get("target", TARGET_PROMPT),
                case_sensitive=entry.get("case_sensitive", False),
                cycle=entry.get("cycle", True),
                error=entry.get("error"),
            )
        )
    if not rules:
        raise ValueError(f"{path} defines no rules")
    return MockAdapter(
        rules,
        model=data.get("model", DEFAULT_MODEL),
        default_response=data.get("default_response"),
        seed=data.get("seed", 0),
    )
