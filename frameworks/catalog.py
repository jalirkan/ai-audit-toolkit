"""Framework control catalogs and the probe-to-control mapping.

## Original summaries only

Every control here is an identifier plus a one-line summary written for this
project. No text from NIST AI 100-1, ISO/IEC 42001, or Regulation (EU)
2024/1689 is reproduced (D-003). The summaries describe what a control is
*about*, which is a fact, in wording that is this project's own.

That rule is enforced, not just stated: :data:`MAX_SUMMARY_CHARS` caps summary
length and a test asserts every catalog entry obeys it. A one-line paraphrase
fits comfortably; pasted framework text does not, so the failure mode announces
itself at test time rather than at publication time.

## The catalogs are partial and dated

Each catalog lists only the controls this toolkit can produce technical
evidence for, carries the date its identifiers were last checked, and says so
in its own ``note``. A control's absence is not a statement about it. Framework
identifiers change between editions; ``ids_verified`` is there so a reader can
see how stale the mapping might be.

## A mapping is not a compliance claim

Mapping a probe to a control asserts that running the procedure produces
evidence *relevant* to that control. It never asserts the control is satisfied.
Controls generally require governance, policy, and documentation that no test
harness can supply, and a probe that runs and fails is still "mapped". Coverage
in :mod:`frameworks.coverage` therefore reports the outcome alongside the
mapping, and separates "no evidence" from "evidence, with exceptions".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1

DATA_DIR = Path(__file__).resolve().parent / "data"
MAPPINGS_FILE = DATA_DIR / "mappings.json"

#: A one-line original summary fits well inside this. Pasted framework text
#: does not, which is the point -- see the module docstring.
MAX_SUMMARY_CHARS = 220

SOURCE_PROBE_PREFIX = "probe:"
SOURCE_CAPABILITY_PREFIX = "capability:"

#: Toolkit capabilities that are not probes but still evidence controls.
CAPABILITY_EVIDENCE_JOURNAL = "evidence-journal"
CAPABILITY_DRIFT_MONITORING = "drift-monitoring"
CAPABILITY_WORKPAPERS = "workpapers"
KNOWN_CAPABILITIES = frozenset(
    {
        CAPABILITY_EVIDENCE_JOURNAL,
        CAPABILITY_DRIFT_MONITORING,
        CAPABILITY_WORKPAPERS,
    }
)

__all__ = [
    "SCHEMA_VERSION",
    "MAX_SUMMARY_CHARS",
    "DATA_DIR",
    "CAPABILITY_EVIDENCE_JOURNAL",
    "CAPABILITY_DRIFT_MONITORING",
    "CAPABILITY_WORKPAPERS",
    "KNOWN_CAPABILITIES",
    "Control",
    "Framework",
    "ControlReference",
    "MappingSet",
    "load_frameworks",
    "load_mappings",
    "probe_source",
    "capability_source",
]


def probe_source(probe_id: str) -> str:
    return f"{SOURCE_PROBE_PREFIX}{probe_id}"


def capability_source(capability: str) -> str:
    return f"{SOURCE_CAPABILITY_PREFIX}{capability}"


@dataclass(frozen=True)
class Control:
    """One control reference: an identifier and an original one-line summary."""

    id: str
    summary: str
    topic: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("control requires an id")
        if not self.summary:
            raise ValueError(f"control {self.id!r} requires a summary")
        if len(self.summary) > MAX_SUMMARY_CHARS:
            raise ValueError(
                f"control {self.id!r} summary is {len(self.summary)} characters, "
                f"over the {MAX_SUMMARY_CHARS} limit. Summaries are one-line "
                "originals; if this is quoted framework text it cannot be "
                "included at all (D-003)."
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Control":
        return cls(
            id=data["id"], summary=data["summary"], topic=data.get("topic", "")
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "summary": self.summary, "topic": self.topic}


@dataclass(frozen=True)
class Framework:
    """A partial, dated catalog of one framework's control references."""

    id: str
    name: str
    publication: str
    controls: Tuple[Control, ...]
    note: str = ""
    partial: bool = True
    ids_verified: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("framework requires an id")
        if not self.controls:
            raise ValueError(f"framework {self.id!r} has no controls")
        object.__setattr__(self, "controls", tuple(self.controls))
        seen = [c.id for c in self.controls]
        duplicates = {c for c in seen if seen.count(c) > 1}
        if duplicates:
            raise ValueError(
                f"framework {self.id!r} has duplicate control ids: {sorted(duplicates)}"
            )
        if self.schema_version > SCHEMA_VERSION:
            raise ValueError(
                f"framework catalog schema version {self.schema_version} is newer "
                f"than this build understands ({SCHEMA_VERSION})"
            )

    def control(self, control_id: str) -> Optional[Control]:
        for c in self.controls:
            if c.id == control_id:
                return c
        return None

    @property
    def control_ids(self) -> List[str]:
        return [c.id for c in self.controls]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Framework":
        return cls(
            id=data["id"],
            name=data["name"],
            publication=data["publication"],
            controls=tuple(Control.from_dict(c) for c in data["controls"]),
            note=data.get("note", ""),
            partial=data.get("partial", True),
            ids_verified=data.get("ids_verified", ""),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "publication": self.publication,
            "partial": self.partial,
            "ids_verified": self.ids_verified,
            "note": self.note,
            "controls": [c.to_dict() for c in self.controls],
        }

    def citation(self) -> str:
        """How to refer to this catalog in a report, caveats included."""
        scope = "partial catalog" if self.partial else "catalog"
        verified = f", identifiers checked {self.ids_verified}" if self.ids_verified else ""
        return f"{self.name} ({self.publication}) -- {scope}{verified}"


@dataclass(frozen=True)
class ControlReference:
    """A claim that some capability evidences a control, and the argument for it."""

    framework: str
    control_id: str
    rationale: str

    def __post_init__(self) -> None:
        if not self.rationale:
            raise ValueError(
                f"mapping to {self.framework}/{self.control_id} needs a rationale; "
                "an unargued mapping is not defensible in review"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ControlReference":
        return cls(
            framework=data["framework"],
            control_id=data["control_id"],
            rationale=data["rationale"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "framework": self.framework,
            "control_id": self.control_id,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class MappingSet:
    """Source -> control references, where a source is a probe or a capability."""

    by_source: Dict[str, Tuple[ControlReference, ...]]
    note: str = ""

    def references_for(self, source: str) -> Tuple[ControlReference, ...]:
        return self.by_source.get(source, ())

    def sources_for(self, framework: str, control_id: str) -> List[str]:
        return sorted(
            source
            for source, references in self.by_source.items()
            for r in references
            if r.framework == framework and r.control_id == control_id
        )

    @property
    def probe_ids(self) -> List[str]:
        return sorted(
            s[len(SOURCE_PROBE_PREFIX):]
            for s in self.by_source
            if s.startswith(SOURCE_PROBE_PREFIX)
        )

    @property
    def capabilities(self) -> List[str]:
        return sorted(
            s[len(SOURCE_CAPABILITY_PREFIX):]
            for s in self.by_source
            if s.startswith(SOURCE_CAPABILITY_PREFIX)
        )


def load_frameworks(directory: Optional[Path] = None) -> Dict[str, Framework]:
    """Load every framework catalog in ``directory`` (default: bundled data)."""
    root = Path(directory) if directory else DATA_DIR
    frameworks: Dict[str, Framework] = {}
    for path in sorted(root.glob("*.json")):
        if path.name == MAPPINGS_FILE.name:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        framework = Framework.from_dict(data)
        if framework.id in frameworks:
            raise ValueError(f"duplicate framework id {framework.id!r} in {path}")
        frameworks[framework.id] = framework
    return frameworks


def load_mappings(path: Optional[Path] = None) -> MappingSet:
    """Load the source-to-control mapping (default: bundled mappings.json)."""
    target = Path(path) if path else MAPPINGS_FILE
    data = json.loads(target.read_text(encoding="utf-8"))
    by_source: Dict[str, Tuple[ControlReference, ...]] = {}
    for entry in data["mappings"]:
        source = entry["source"]
        if source in by_source:
            raise ValueError(f"duplicate mapping source {source!r}")
        if not (
            source.startswith(SOURCE_PROBE_PREFIX)
            or source.startswith(SOURCE_CAPABILITY_PREFIX)
        ):
            raise ValueError(
                f"mapping source {source!r} must start with "
                f"{SOURCE_PROBE_PREFIX!r} or {SOURCE_CAPABILITY_PREFIX!r}"
            )
        by_source[source] = tuple(
            ControlReference.from_dict(r) for r in entry["references"]
        )
    return MappingSet(by_source=by_source, note=data.get("note", ""))
