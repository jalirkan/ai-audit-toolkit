"""Load and validate a golden RAG faithfulness dataset.

The file is data, not code: sources, questions, labeled gold answers. Labels
judge what the *screen* should conclude about each gold answer, so the harness
can plant-signal-test the lexical check offline without a model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple, Union

from probes.citation import CitationCase

SCHEMA_VERSION = 1

EXPECT_FAITHFUL = "faithful"
EXPECT_UNFAITHFUL = "unfaithful"
EXPECTATIONS = frozenset({EXPECT_FAITHFUL, EXPECT_UNFAITHFUL})

__all__ = [
    "SCHEMA_VERSION",
    "EXPECT_FAITHFUL",
    "EXPECT_UNFAITHFUL",
    "EXPECTATIONS",
    "GoldenItem",
    "GoldenDataset",
    "load_dataset",
]


@dataclass(frozen=True)
class GoldenItem:
    """One question with a gold answer and the screen outcome it should yield."""

    id: str
    question: str
    gold_answer: str
    expect: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("golden item requires an id")
        if not self.question:
            raise ValueError(f"item {self.id!r} requires a question")
        if not self.gold_answer:
            raise ValueError(f"item {self.id!r} requires a gold_answer")
        if self.expect not in EXPECTATIONS:
            raise ValueError(
                f"item {self.id!r} expect must be one of {sorted(EXPECTATIONS)}, "
                f"got {self.expect!r}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "gold_answer": self.gold_answer,
            "expect": self.expect,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GoldenItem":
        return cls(
            id=data["id"],
            question=data["question"],
            gold_answer=data["gold_answer"],
            expect=data["expect"],
        )


@dataclass(frozen=True)
class GoldenDataset:
    """Closed-context sources plus planted Q/A items for the faithfulness screen."""

    id: str
    sources: Tuple[str, ...]
    items: Tuple[GoldenItem, ...]
    description: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("golden dataset requires an id")
        if self.schema_version > SCHEMA_VERSION:
            raise ValueError(
                f"dataset schema version {self.schema_version} is newer than "
                f"this build understands ({SCHEMA_VERSION})"
            )
        if not self.sources:
            raise ValueError(f"dataset {self.id!r} requires at least one source")
        if not self.items:
            raise ValueError(f"dataset {self.id!r} requires at least one item")
        ids = [item.id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError(f"dataset {self.id!r} has duplicate item ids: {ids}")

    def as_citation_case(self) -> CitationCase:
        """Live path: same sources and questions, scored by the citation probe."""
        return CitationCase(
            id=self.id,
            sources=self.sources,
            questions=tuple(item.question for item in self.items),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "description": self.description,
            "sources": list(self.sources),
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GoldenDataset":
        if "expect" in data and "items" not in data:
            raise ValueError(
                "dataset payload looks like a single item; expected top-level "
                "'sources' and 'items'"
            )
        raw_items = data.get("items")
        if raw_items is None:
            raise ValueError("dataset requires an 'items' list")
        return cls(
            id=data["id"],
            sources=tuple(data.get("sources") or ()),
            items=tuple(GoldenItem.from_dict(item) for item in raw_items),
            description=data.get("description", ""),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


def load_dataset(path: Union[str, Path]) -> GoldenDataset:
    """Read a golden dataset JSON file."""
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read dataset {target}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"dataset {target} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"dataset {target} must be a JSON object")
    return GoldenDataset.from_dict(payload)
