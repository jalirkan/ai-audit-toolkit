"""Labelled baselines: the reference a later run is compared against.

A baseline is a whole battery result, stored under a name the engagement
chooses ("q3-2026-preupgrade", "vendor-model-v2"). Comparison needs the
measurements themselves, not a summary, so the full result is kept.

Saving refuses to overwrite unless asked explicitly. A baseline is the fixed
point a drift claim rests on; silently replacing it would rewrite the past and
make "no drift detected" true by construction.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Union

from battery.runner import BatteryResult
from core.evidence import utc_now_iso

SCHEMA_VERSION = 1

#: Labels become filenames, so they are restricted rather than escaped -- no
#: separators, no traversal, no surprises about which file was written.
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

DEFAULT_BASELINE_DIR = "baselines"

__all__ = [
    "SCHEMA_VERSION",
    "LABEL_PATTERN",
    "DEFAULT_BASELINE_DIR",
    "Baseline",
    "BaselineStore",
    "validate_label",
]


def validate_label(label: str) -> str:
    if not LABEL_PATTERN.match(label or ""):
        raise ValueError(
            f"invalid baseline label {label!r}: use 1-64 characters from "
            "letters, digits, dot, underscore, and hyphen, starting with a "
            "letter or digit"
        )
    return label


@dataclass(frozen=True)
class Baseline:
    """A stored reference run."""

    label: str
    saved_at: str
    result: BatteryResult
    note: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_label(self.label)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "label": self.label,
            "saved_at": self.saved_at,
            "note": self.note,
            "result": self.result.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Baseline":
        version = data.get("schema_version", SCHEMA_VERSION)
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"baseline schema version {version} is newer than this build "
                f"understands ({SCHEMA_VERSION})"
            )
        return cls(
            label=data["label"],
            saved_at=data["saved_at"],
            result=BatteryResult.from_dict(data["result"]),
            note=data.get("note", ""),
            schema_version=version,
        )


class BaselineStore:
    """Baselines on disk, one JSON file per label."""

    def __init__(self, root: Union[str, Path] = DEFAULT_BASELINE_DIR) -> None:
        self.root = Path(root)

    def path_for(self, label: str) -> Path:
        return self.root / f"{validate_label(label)}.json"

    def exists(self, label: str) -> bool:
        return self.path_for(label).exists()

    def labels(self) -> List[str]:
        if not self.root.exists():
            return []
        return sorted(p.stem for p in self.root.glob("*.json"))

    def save(
        self,
        label: str,
        result: BatteryResult,
        *,
        note: str = "",
        overwrite: bool = False,
        saved_at: str = "",
    ) -> Path:
        """Write a baseline. Refuses to clobber an existing label."""
        path = self.path_for(label)
        if path.exists() and not overwrite:
            raise FileExistsError(
                f"baseline {label!r} already exists at {path}; pass "
                "overwrite=True to replace it deliberately"
            )
        baseline = Baseline(
            label=label,
            saved_at=saved_at or utc_now_iso(),
            result=result,
            note=note,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(baseline.to_dict(), indent=2) + "\n", encoding="utf-8"
        )
        return path

    def load(self, label: str) -> Baseline:
        path = self.path_for(label)
        if not path.exists():
            known = self.labels()
            raise FileNotFoundError(
                f"no baseline named {label!r} in {self.root}; "
                f"available: {known if known else 'none'}"
            )
        return Baseline.from_dict(json.loads(path.read_text(encoding="utf-8")))
