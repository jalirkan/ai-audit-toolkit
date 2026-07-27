"""Named suites of probes, defined as data.

A battery is a JSON file naming which procedures to perform and with what
configuration. Keeping it as data rather than code means the suite an
engagement actually ran is a file that can be attached to the workpapers,
diffed against last quarter's, and re-run verbatim.

JSON rather than YAML: YAML would be a dependency, and D-001 says a dependency
has to earn its place. Comments are the one thing genuinely missed, so specs
carry ``description`` fields instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple, Union

# Importing the package registers every built-in probe, so a spec naming one
# can be validated the moment it is loaded.
import probes  # noqa: F401
from probes.base import Probe, get_probe

SCHEMA_VERSION = 1

__all__ = ["SCHEMA_VERSION", "ProbeSpec", "BatterySpec"]


@dataclass(frozen=True)
class ProbeSpec:
    """One probe and the configuration to run it with."""

    probe_id: str
    config: Dict[str, Any] = field(default_factory=dict)
    #: Optional human label, used in reports when one battery runs the same
    #: probe more than once with different settings.
    label: str = ""

    def __post_init__(self) -> None:
        if not self.probe_id:
            raise ValueError("probe spec requires a probe_id")
        # Fail at load time, with the list of what is available, rather than
        # part-way through a run.
        get_probe(self.probe_id)

    @property
    def display_name(self) -> str:
        return self.label or get_probe(self.probe_id).title or self.probe_id

    def build(self) -> Probe:
        """Instantiate the probe. Configuration errors surface here."""
        probe_cls = get_probe(self.probe_id)
        try:
            return probe_cls.from_config(dict(self.config))
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(
                f"cannot configure probe {self.probe_id!r}: {exc}"
            ) from exc

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProbeSpec":
        return cls(
            probe_id=data["probe_id"],
            config=dict(data.get("config") or {}),
            label=data.get("label", ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "config": dict(self.config),
            "label": self.label,
        }


@dataclass(frozen=True)
class BatterySpec:
    """A named, ordered suite of probes."""

    name: str
    probes: Tuple[ProbeSpec, ...]
    description: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("battery requires a name")
        if not self.probes:
            raise ValueError(f"battery {self.name!r} contains no probes")
        object.__setattr__(self, "probes", tuple(self.probes))
        if self.schema_version > SCHEMA_VERSION:
            raise ValueError(
                f"battery schema version {self.schema_version} is newer than "
                f"this build understands ({SCHEMA_VERSION})"
            )

    @property
    def probe_ids(self) -> List[str]:
        """Distinct probe ids in the suite, in first-appearance order."""
        seen: List[str] = []
        for spec in self.probes:
            if spec.probe_id not in seen:
                seen.append(spec.probe_id)
        return seen

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BatterySpec":
        return cls(
            name=data["name"],
            probes=tuple(ProbeSpec.from_dict(p) for p in data.get("probes", ())),
            description=data.get("description", ""),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )

    @classmethod
    def load(cls, path: Union[str, Path]) -> "BatterySpec":
        """Read a battery spec from a JSON file."""
        text = Path(path).read_text(encoding="utf-8")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc
        try:
            return cls.from_dict(data)
        except KeyError as exc:
            raise ValueError(f"{path} is missing required field {exc}") from exc

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "description": self.description,
            "probes": [p.to_dict() for p in self.probes],
        }

    def save(self, path: Union[str, Path]) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8"
        )

    def build_all(self) -> List[Probe]:
        return [spec.build() for spec in self.probes]
