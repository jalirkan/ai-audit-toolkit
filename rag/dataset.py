"""Load and validate a golden RAG faithfulness dataset.

The file is data, not code: sources, questions, labeled gold answers. Labels
judge what the *screen* should conclude about each gold answer, so the harness
can plant-signal-test the lexical check offline without a model.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple, Union

from probes.citation import CitationCase

SCHEMA_VERSION = 1

EXPECT_FAITHFUL = "faithful"
EXPECT_UNFAITHFUL = "unfaithful"
EXPECTATIONS = frozenset({EXPECT_FAITHFUL, EXPECT_UNFAITHFUL})

#: What kind of case an item exercises. Reported as strata, because an overall
#: accuracy figure is a weighted average over whatever mix the dataset author
#: happened to write -- add ten more verbatim items and the number climbs
#: without the screen improving at all. Per-category accuracy cannot be moved
#: that way, and it shows which failure modes are real.
CATEGORY_VERBATIM = "verbatim"
CATEGORY_PARAPHRASE = "paraphrase"
CATEGORY_ABSTENTION = "abstention"
CATEGORY_UNSOURCED_NUMBER = "unsourced-number"
CATEGORY_NEGATION_FLIP = "negation-flip"
CATEGORY_ENTITY_SWAP = "entity-swap"
CATEGORY_TERM_SWAP = "term-swap"
CATEGORY_OFF_TOPIC = "off-topic"
CATEGORY_UNSPECIFIED = "unspecified"
CATEGORIES = frozenset(
    {
        CATEGORY_VERBATIM,
        CATEGORY_PARAPHRASE,
        CATEGORY_ABSTENTION,
        CATEGORY_UNSOURCED_NUMBER,
        CATEGORY_NEGATION_FLIP,
        CATEGORY_ENTITY_SWAP,
        CATEGORY_TERM_SWAP,
        CATEGORY_OFF_TOPIC,
        CATEGORY_UNSPECIFIED,
    }
)

__all__ = [
    "SCHEMA_VERSION",
    "EXPECT_FAITHFUL",
    "EXPECT_UNFAITHFUL",
    "EXPECTATIONS",
    "CATEGORIES",
    "CATEGORY_VERBATIM",
    "CATEGORY_PARAPHRASE",
    "CATEGORY_ABSTENTION",
    "CATEGORY_UNSOURCED_NUMBER",
    "CATEGORY_NEGATION_FLIP",
    "CATEGORY_ENTITY_SWAP",
    "CATEGORY_TERM_SWAP",
    "CATEGORY_OFF_TOPIC",
    "CATEGORY_UNSPECIFIED",
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
    #: Which failure mode this item exercises. Optional so older datasets still
    #: load; they report as a single ``unspecified`` stratum, which is itself a
    #: useful signal that the dataset cannot say where the screen breaks.
    category: str = CATEGORY_UNSPECIFIED
    #: Set when the screen is *known* to get this item wrong and the dataset
    #: keeps it anyway. Documenting a blind spot is worth more than deleting
    #: the item that reveals it.
    known_screen_miss: bool = False
    note: str = ""

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
        if self.category not in CATEGORIES:
            raise ValueError(
                f"item {self.id!r} category must be one of {sorted(CATEGORIES)}, "
                f"got {self.category!r}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "gold_answer": self.gold_answer,
            "expect": self.expect,
            "category": self.category,
            "known_screen_miss": self.known_screen_miss,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GoldenItem":
        return cls(
            id=data["id"],
            question=data["question"],
            gold_answer=data["gold_answer"],
            expect=data["expect"],
            category=data.get("category", CATEGORY_UNSPECIFIED),
            known_screen_miss=bool(data.get("known_screen_miss", False)),
            note=data.get("note", ""),
        )


@dataclass(frozen=True)
class GoldenDataset:
    """Closed-context sources plus planted Q/A items for the faithfulness screen."""

    id: str
    sources: Tuple[str, ...]
    items: Tuple[GoldenItem, ...]
    description: str = ""
    schema_version: int = SCHEMA_VERSION
    #: sha256 of the file this was read from, set by `load_dataset`. Empty for a
    #: dataset built in memory, which has no bytes to name.
    #:
    #: `id` is an identity and not a version: it stayed "northwind-rag-golden"
    #: while the set grew from 56 items to 128, so a result naming only the id
    #: cannot say which dataset produced it. The hash can.
    content_sha256: str = ""

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

    @property
    def categories(self) -> Tuple[str, ...]:
        """Distinct categories present, in first-appearance order."""
        seen: list = []
        for item in self.items:
            if item.category not in seen:
                seen.append(item.category)
        return tuple(seen)

    def items_in(self, category: str) -> Tuple["GoldenItem", ...]:
        return tuple(i for i in self.items if i.category == category)

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
    # Hashed as read, before parsing: the bytes are what a consumer can compare
    # against, and a re-serialised copy would not match the file on disk.
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return replace(GoldenDataset.from_dict(payload), content_sha256=f"sha256:{digest}")
