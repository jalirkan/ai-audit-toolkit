"""Tamper-evident evidence journal.

See ``journal.store`` for what the hash chain does and does not prove.
"""

from journal.store import (  # noqa: F401
    GENESIS_HASH,
    KIND_EVIDENCE,
    KIND_NOTE,
    KIND_RUN,
    Journal,
    JournalEntry,
    Problem,
    VerificationResult,
)

__all__ = [
    "GENESIS_HASH",
    "KIND_EVIDENCE",
    "KIND_NOTE",
    "KIND_RUN",
    "Journal",
    "JournalEntry",
    "Problem",
    "VerificationResult",
]
