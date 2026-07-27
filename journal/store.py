"""Hash-chained, append-only evidence journal on SQLite.

Every entry stores the hash of the entry before it, so the log is a chain: an
edit to any historical row changes that row's hash, which breaks the link the
next row asserts, and :meth:`Journal.verify` reports where. Deleting a row
leaves a gap in the sequence and a broken link. Reordering rows breaks the
links too. That is the whole mechanism, and it is what lets an auditor say the
evidence has not been altered since it was recorded.

## What this does and does not prove

A hash chain detects *edits to history*. It does not, on its own, detect a
**full rewrite**: anyone who can write to the database file can rebuild every
row and every hash from scratch, producing a chain that verifies perfectly.

The defence against that is anchoring -- recording the head hash somewhere the
person who controls the database does not, such as a reviewer's own notes, a
ticket, a signed email, or another system's log. :meth:`Journal.head` exists
for exactly that, and :meth:`Journal.verify` accepts an ``expected_head`` to
check against. A journal whose head has never been anchored proves internal
consistency and nothing about the intent of whoever held the file. Saying so
plainly is more useful than implying otherwise.

## Defence in depth

The table carries triggers that abort any UPDATE or DELETE. They stop the
toolkit and casual SQL from modifying history at all, so the chain is a
backstop rather than the only line of defence. A determined party can drop the
triggers -- the tests do exactly that to simulate tampering -- at which point
the chain is what catches them.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

from core.canonical import canonical_json, content_hash, text_hash
from core.evidence import Evidence, utc_now_iso

SCHEMA_VERSION = 1

#: Predecessor hash of the first entry. Distinct from any real hash, and fixed
#: so an empty journal has a well-defined head.
GENESIS_HASH = "sha256:" + "0" * 64

KIND_EVIDENCE = "evidence"
KIND_RUN = "run"
KIND_NOTE = "note"
KINDS = frozenset({KIND_EVIDENCE, KIND_RUN, KIND_NOTE})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
    seq          INTEGER PRIMARY KEY,
    recorded_at  TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    payload      TEXT    NOT NULL,
    payload_hash TEXT    NOT NULL,
    prev_hash    TEXT    NOT NULL,
    entry_hash   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS journal_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS journal_no_update
BEFORE UPDATE ON journal
BEGIN
    SELECT RAISE(ABORT, 'journal is append-only: entries cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS journal_no_delete
BEFORE DELETE ON journal
BEGIN
    SELECT RAISE(ABORT, 'journal is append-only: entries cannot be deleted');
END;
"""

__all__ = [
    "SCHEMA_VERSION",
    "GENESIS_HASH",
    "KIND_EVIDENCE",
    "KIND_RUN",
    "KIND_NOTE",
    "JournalEntry",
    "Problem",
    "VerificationResult",
    "Journal",
]


@dataclass(frozen=True)
class JournalEntry:
    """One recorded row, with the links that bind it to its neighbours."""

    seq: int
    recorded_at: str
    kind: str
    payload: str
    payload_hash: str
    prev_hash: str
    entry_hash: str

    def parsed(self) -> Any:
        import json

        return json.loads(self.payload)

    def as_evidence(self) -> Evidence:
        if self.kind != KIND_EVIDENCE:
            raise ValueError(f"entry {self.seq} is a {self.kind}, not evidence")
        return Evidence.from_dict(self.parsed())


def _compute_entry_hash(
    seq: int, recorded_at: str, kind: str, payload_hash: str, prev_hash: str
) -> str:
    """Bind every column of an entry, plus its predecessor, into one hash.

    ``seq`` is included so a row cannot be moved to another position without
    detection, and ``recorded_at`` so a timestamp cannot be quietly adjusted.
    """
    return content_hash(
        {
            "seq": seq,
            "recorded_at": recorded_at,
            "kind": kind,
            "payload_hash": payload_hash,
            "prev_hash": prev_hash,
        }
    )


@dataclass(frozen=True)
class Problem:
    """One defect found during verification."""

    seq: Optional[int]
    code: str
    detail: str

    def __str__(self) -> str:
        where = f"entry {self.seq}" if self.seq is not None else "journal"
        return f"{where}: {self.detail}"


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of walking the chain."""

    ok: bool
    entries_checked: int
    problems: Tuple[Problem, ...] = ()
    head_hash: str = GENESIS_HASH

    def summary(self) -> str:
        if self.ok:
            return (
                f"Chain intact across {self.entries_checked} entries. "
                f"Head {self.head_hash[7:19]}."
            )
        return (
            f"Chain BROKEN: {len(self.problems)} problem(s) across "
            f"{self.entries_checked} entries. First: {self.problems[0]}"
        )


class Journal:
    """Append-only, hash-chained store for evidence and run records."""

    def __init__(self, path: Union[str, Path] = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO journal_meta (key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        self._conn.commit()

    # -- lifecycle ------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Journal":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- reading --------------------------------------------------------------

    def __len__(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM journal").fetchone()
        return int(row["c"])

    def entries(self) -> List[JournalEntry]:
        rows = self._conn.execute("SELECT * FROM journal ORDER BY seq").fetchall()
        return [JournalEntry(**dict(r)) for r in rows]

    def __iter__(self) -> Iterator[JournalEntry]:
        return iter(self.entries())

    def entries_of_kind(self, kind: str) -> List[JournalEntry]:
        rows = self._conn.execute(
            "SELECT * FROM journal WHERE kind = ? ORDER BY seq", (kind,)
        ).fetchall()
        return [JournalEntry(**dict(r)) for r in rows]

    def evidence(self) -> List[Evidence]:
        return [e.as_evidence() for e in self.entries_of_kind(KIND_EVIDENCE)]

    def head(self) -> str:
        """Hash of the most recent entry -- the value worth anchoring elsewhere.

        Returns :data:`GENESIS_HASH` for an empty journal.
        """
        row = self._conn.execute(
            "SELECT entry_hash FROM journal ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["entry_hash"] if row else GENESIS_HASH

    # -- writing --------------------------------------------------------------

    def append(
        self,
        kind: str,
        payload: Any,
        *,
        recorded_at: Optional[str] = None,
    ) -> JournalEntry:
        """Append one entry and return it.

        The read of the current head and the write of the new row happen inside
        one immediate transaction, so two concurrent appends cannot both chain
        off the same predecessor.
        """
        if kind not in KINDS:
            raise ValueError(f"unknown journal kind {kind!r}; expected one of {sorted(KINDS)}")

        payload_text = canonical_json(payload)
        payload_hash = text_hash(payload_text)
        stamp = recorded_at or utc_now_iso()

        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT seq, entry_hash FROM journal ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            seq = (row["seq"] + 1) if row else 1
            prev_hash = row["entry_hash"] if row else GENESIS_HASH
            entry_hash = _compute_entry_hash(
                seq, stamp, kind, payload_hash, prev_hash
            )
            self._conn.execute(
                "INSERT INTO journal "
                "(seq, recorded_at, kind, payload, payload_hash, prev_hash, entry_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (seq, stamp, kind, payload_text, payload_hash, prev_hash, entry_hash),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        return JournalEntry(
            seq=seq,
            recorded_at=stamp,
            kind=kind,
            payload=payload_text,
            payload_hash=payload_hash,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )

    def append_evidence(self, evidence: Evidence) -> JournalEntry:
        return self.append(KIND_EVIDENCE, evidence.to_dict())

    def append_all_evidence(self, items: Iterable[Evidence]) -> List[JournalEntry]:
        return [self.append_evidence(e) for e in items]

    def append_run(self, run: Dict[str, Any]) -> JournalEntry:
        return self.append(KIND_RUN, run)

    def append_note(self, text: str, **fields: Any) -> JournalEntry:
        return self.append(KIND_NOTE, {"text": text, **fields})

    # -- verification ---------------------------------------------------------

    def verify(self, *, expected_head: Optional[str] = None) -> VerificationResult:
        """Walk the chain and report every defect found.

        Checks, per entry: the sequence is contiguous from 1, the stored
        payload still hashes to its recorded ``payload_hash``, the recomputed
        entry hash matches, and ``prev_hash`` matches the previous entry's hash.

        Verification does not stop at the first problem. An auditor wants the
        full list of what is wrong, not just the earliest symptom.
        """
        problems: List[Problem] = []
        entries = self.entries()
        prev_hash = GENESIS_HASH
        expected_seq = 1

        for entry in entries:
            if entry.seq != expected_seq:
                problems.append(
                    Problem(
                        entry.seq,
                        "sequence-gap",
                        f"expected sequence {expected_seq} but found {entry.seq}; "
                        "an entry has been removed or inserted",
                    )
                )
                expected_seq = entry.seq

            recomputed_payload_hash = text_hash(entry.payload)
            if recomputed_payload_hash != entry.payload_hash:
                problems.append(
                    Problem(
                        entry.seq,
                        "payload-modified",
                        "stored payload no longer matches its recorded hash",
                    )
                )

            if entry.prev_hash != prev_hash:
                problems.append(
                    Problem(
                        entry.seq,
                        "broken-link",
                        f"claims predecessor {entry.prev_hash[7:19]} but the "
                        f"preceding entry hashes to {prev_hash[7:19]}",
                    )
                )

            recomputed_entry_hash = _compute_entry_hash(
                entry.seq,
                entry.recorded_at,
                entry.kind,
                entry.payload_hash,
                entry.prev_hash,
            )
            if recomputed_entry_hash != entry.entry_hash:
                problems.append(
                    Problem(
                        entry.seq,
                        "entry-hash-mismatch",
                        "entry hash does not match its own contents",
                    )
                )
                # Continue the walk from the hash actually stored, so one bad
                # row does not cascade a broken-link report onto every entry
                # after it. The problem list stays readable.
            prev_hash = entry.entry_hash
            expected_seq += 1

        head = entries[-1].entry_hash if entries else GENESIS_HASH

        if expected_head is not None and head != expected_head:
            problems.append(
                Problem(
                    None,
                    "head-mismatch",
                    f"head is {head[7:19]} but the anchored value was "
                    f"{expected_head[7:19]}; entries have been added or the "
                    "journal has been rebuilt",
                )
            )

        return VerificationResult(
            ok=not problems,
            entries_checked=len(entries),
            problems=tuple(problems),
            head_hash=head,
        )
