"""Canonical JSON encoding and content hashing.

Audit artifacts get hashed, stored, and later re-hashed to prove they have not
changed. That only works if the same logical object always serializes to the
same bytes, so serialization is pinned here rather than left to each caller's
``json.dumps`` defaults:

- ``sort_keys=True``      -- key order cannot depend on dict insertion order.
- tight separators        -- no incidental whitespace in the hashed bytes.
- ``ensure_ascii=False``  -- text stays readable; bytes are fixed by UTF-8.
- ``allow_nan=False``     -- NaN/Infinity are not JSON and do not survive a
  round-trip, so they are rejected at write time instead of corrupting an
  evidence record that an auditor later tries to re-verify.

The hash is prefixed with its algorithm (``sha256:...``) so a stored chain
remains interpretable if the algorithm is ever changed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

HASH_ALGORITHM = "sha256"
HASH_PREFIX = f"{HASH_ALGORITHM}:"

__all__ = [
    "HASH_ALGORITHM",
    "HASH_PREFIX",
    "canonical_json",
    "canonical_bytes",
    "content_hash",
    "text_hash",
    "is_json_serializable",
]


def canonical_json(obj: Any) -> str:
    """Serialize ``obj`` to the one canonical JSON string for its value."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_bytes(obj: Any) -> bytes:
    """UTF-8 bytes of :func:`canonical_json` -- the input to every hash here."""
    return canonical_json(obj).encode("utf-8")


def content_hash(obj: Any) -> str:
    """Algorithm-prefixed SHA-256 over the canonical encoding of ``obj``."""
    digest = hashlib.sha256(canonical_bytes(obj)).hexdigest()
    return f"{HASH_PREFIX}{digest}"


def text_hash(text: str) -> str:
    """Algorithm-prefixed SHA-256 over the UTF-8 bytes of ``text``.

    Used by the journal to hash exactly the bytes it stored, rather than
    re-encoding a parsed object. Hashing the stored text is strictly stronger:
    it catches an edit that changes the bytes without changing the parsed
    value, which a re-encode would silently forgive.
    """
    return f"{HASH_PREFIX}{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def is_json_serializable(obj: Any) -> bool:
    """True if ``obj`` survives canonical encoding.

    Used to reject unserializable payloads when a record is constructed, which
    is where the offending value can still be traced to its source, rather than
    at journal-write time, which may be many frames away.
    """
    try:
        canonical_json(obj)
    except (TypeError, ValueError):
        return False
    return True
