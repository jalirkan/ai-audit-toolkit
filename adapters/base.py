"""The single interface every model endpoint hides behind.

Deliberately small. A probe needs text in and text out; anything richer
(streaming, tool use, multi-turn state) would have to be reproduced by every
adapter and would widen the surface an auditor has to trust. If a future probe
genuinely needs multi-turn, that is a new method with its own justification,
not a quiet generalization of this one.

**Generation parameters are fixed at adapter construction, not per call.**
That is the whole reason the fingerprint means anything: if temperature could
change between calls inside one probe run, the fingerprint stored alongside the
evidence would describe the run only approximately. A probe that needs a second
configuration constructs a second adapter, and the difference shows up in the
evidence where a reviewer can see it.

The system prompt is the one exception -- it may be overridden per call,
because for some procedures (prompt-injection resistance especially) varying
the system prompt is the procedure. Overridden values are recorded on each
``Trial``, so nothing is hidden; the adapter's *default* system prompt is what
the fingerprint hashes.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Iterable, List, Optional, Sequence

from core.canonical import content_hash
from core.evidence import ModelFingerprint

#: Generation parameters every adapter understands. Adapters may accept more;
#: these are the ones the toolkit reasons about.
#:
#: ``max_tokens`` was raised from 512 to 1024 after a live run truncated an
#: enumerated answer at exactly the old ceiling: the screen then assessed half
#: a sentence and booked an exception the model had not earned. Answers that
#: run long are usually the ones enumerating several sources, which is exactly
#: where faithfulness is most worth measuring (D-046). Changing this changes
#: every fingerprint, so runs either side of it are not comparable.
DEFAULT_PARAMS: Dict[str, Any] = {
    "temperature": 0.0,
    "max_tokens": 1024,
}

__all__ = [
    "DEFAULT_PARAMS",
    "AdapterError",
    "ModelResponse",
    "ModelAdapter",
]


class AdapterError(RuntimeError):
    """A model call failed.

    Raised for transport failures, refused credentials, and scripted mock
    failures alike. Probes catch this and record ``OUTCOME_ERROR`` -- an error
    is a fact about the procedure, never a finding about the model.
    """


@dataclass(frozen=True)
class ModelResponse:
    """One completion, plus what is needed to evidence it."""

    text: str
    model: str
    latency_ms: float = 0.0
    #: The system prompt actually in effect for this call, if any.
    system: Optional[str] = None
    finish_reason: Optional[str] = None
    usage: Dict[str, int] = field(default_factory=dict)
    request_id: Optional[str] = None
    #: Adapter-specific extras. Kept JSON-safe so it can reach the journal.
    raw: Dict[str, Any] = field(default_factory=dict)


class ModelAdapter(abc.ABC):
    """Base class for model endpoints.

    Subclasses set :attr:`name` and :attr:`requires_network` and implement
    :meth:`complete`. Everything else is provided.
    """

    #: Short adapter identifier, recorded in the fingerprint.
    name: ClassVar[str] = "base"
    #: Whether using this adapter reaches the network. The test suite asserts
    #: that everything it exercises is False here, which is how the
    #: offline-by-default guarantee (D-001) stays true rather than aspirational.
    requires_network: ClassVar[bool] = True

    def __init__(
        self,
        *,
        model: str,
        params: Optional[Dict[str, Any]] = None,
        system: Optional[str] = None,
    ) -> None:
        if not model:
            raise ValueError("adapter requires a model identifier")
        merged = dict(DEFAULT_PARAMS)
        merged.update(params or {})
        self.model = model
        self.params: Dict[str, Any] = merged
        self.system = system

    # -- required ------------------------------------------------------------

    @abc.abstractmethod
    def complete(self, prompt: str, *, system: Optional[str] = None) -> ModelResponse:
        """Send ``prompt`` and return the completion.

        ``system=None`` means "use the adapter's default system prompt".
        Raises :class:`AdapterError` if the call cannot be completed.
        """

    # -- provided ------------------------------------------------------------

    def complete_many(
        self,
        prompts: Iterable[str],
        *,
        system: Optional[str] = None,
    ) -> List[ModelResponse]:
        """Sequential convenience wrapper over :meth:`complete`.

        Sequential on purpose: probe results must not depend on scheduling, and
        a fixed call order keeps scripted mocks reproducible.
        """
        return [self.complete(p, system=system) for p in prompts]

    def effective_system(self, system: Optional[str]) -> Optional[str]:
        """Resolve a per-call system prompt against the adapter default."""
        return self.system if system is None else system

    def fingerprint(self) -> ModelFingerprint:
        """What was under test, in a form that can be compared across runs."""
        return ModelFingerprint(
            adapter=self.name,
            model=self.model,
            params=dict(self.params),
            system_prompt_hash=(
                content_hash(self.system) if self.system is not None else None
            ),
        )

    def describe(self) -> str:
        net = "network" if self.requires_network else "offline"
        return f"{self.name}:{self.model} ({net}, {self.fingerprint().short()})"

    def __repr__(self) -> str:  # pragma: no cover - debugging convenience
        return f"<{type(self).__name__} {self.describe()}>"


def assert_offline(adapters: Sequence[ModelAdapter]) -> None:
    """Raise unless every adapter given is offline-safe.

    Used by the test suite and by any code path that promises not to touch the
    network.
    """
    online = [a for a in adapters if a.requires_network]
    if online:
        names = ", ".join(a.describe() for a in online)
        raise AssertionError(f"network-requiring adapters present: {names}")
