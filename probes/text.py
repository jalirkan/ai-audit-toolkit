"""Deterministic text comparison helpers shared by the probes.

Everything here is lexical. There is no semantic model in this toolkit and
pretending otherwise would be the worst thing it could do: a lexical screen
that is *described* as a lexical screen is useful evidence, while the same
screen described as a judgment of meaning is a misleading one.

Concretely, these functions over-flag correct paraphrase (same meaning,
different words) and under-flag fluent fabrication assembled from source
vocabulary. Probes that use them say so in their procedure text and mark their
output as requiring reviewer inspection of exceptions.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Set

#: Deliberately small. A large stopword list starts making semantic decisions
#: about what counts as content, which is exactly what this module refuses to
#: claim it can do. These are function words whose presence or absence says
#: nothing about whether two answers agree.
STOPWORDS: frozenset = frozenset(
    """
    a an the and or but if then than that this these those of in on at to for
    from by with without into onto is are was were be been being am do does
    did done have has had having it its as not no nor so such there here
    which who whom whose what when where why how you your yours i me my we our
    us they them their he she his her will would can could should may might
    must shall about over under again further once also very just
    """.split()
)

#: Words, numbers (including decimals and thousands separators), and percentages.
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*%?")

#: Sentence break: terminal punctuation followed by whitespace, or a newline.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

#: Leading list markers stripped before a sentence is considered.
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*•]|\(?\d+[.)])\s*")

_NUMERIC_RE = re.compile(r"^[0-9]")

#: Words that flip the polarity of a claim. These are *also* stopwords, which
#: is correct for similarity -- "the cat sat" and "the cat did not sit" are
#: about the same subject -- and catastrophic for support checking, where they
#: invert the meaning entirely. Polarity is therefore read from the raw text by
#: :func:`is_negated`, separately from tokenisation.
NEGATION_CUES: frozenset = frozenset(
    """
    not no never cannot neither nor without none nobody nothing
    unable excluded prohibited forbidden refuses refuse denied
    """.split()
)

__all__ = [
    "STOPWORDS",
    "NEGATION_CUES",
    "is_negated",
    "tokenize",
    "content_tokens",
    "jaccard",
    "coverage",
    "split_sentences",
    "numbers_in",
    "normalize_for_match",
    "contains_normalized",
]


def tokenize(text: str) -> List[str]:
    """Lowercase word/number tokens, punctuation discarded.

    ``"Revenue rose 3.5% in 2025."`` -> ``["revenue", "rose", "3.5%", "in", "2025"]``
    """
    return _TOKEN_RE.findall(text.lower())


def content_tokens(text: str) -> Set[str]:
    """Token set with stopwords removed, for overlap comparisons."""
    return {t for t in tokenize(text) if t not in STOPWORDS}


def is_negated(text: str) -> bool:
    """True if ``text`` carries a negation cue.

    A blunt instrument: it detects that a negation is present, not what it
    scopes over. "Northwind does not ship animals, but does ship freight" reads
    as negated in full. It exists because the alternative -- ignoring polarity,
    which is what dropping ``not`` as a stopword amounts to -- lets a claim and
    its exact opposite score as an equally good match against the same source.
    For a support check that is the worst error available, so a coarse signal
    beats none.

    Contractions are caught via ``n't`` because the tokenizer splits them.
    """
    lowered = text.lower()
    if "n't" in lowered or "n’t" in lowered:
        return True
    return any(token in NEGATION_CUES for token in tokenize(lowered))


def jaccard(a: Set[str], b: Set[str]) -> float:
    """Intersection over union. Two empty sets are treated as identical (1.0)."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def coverage(claim: Set[str], source: Set[str]) -> float:
    """Fraction of ``claim``'s tokens that appear in ``source``.

    Asymmetric on purpose: the question is whether the source accounts for the
    claim, not whether the two are similar in length.
    """
    if not claim:
        return 1.0
    return len(claim & source) / len(claim)


def split_sentences(text: str) -> List[str]:
    """Split into candidate sentences, stripping list markers.

    A regex splitter mishandles abbreviations ("Inc. reported...") and decimals
    are protected only because the tokenizer runs after. Good enough to
    enumerate claims for review; not a parser.
    """
    parts: List[str] = []
    for raw in _SENTENCE_SPLIT_RE.split(text):
        cleaned = _LIST_MARKER_RE.sub("", raw).strip()
        if cleaned:
            parts.append(cleaned)
    return parts


def numbers_in(text: str) -> Set[str]:
    """Numeric tokens, normalized so ``1,200`` and ``1200`` compare equal.

    Numbers get their own check because a fabricated figure is the failure mode
    that matters most in a cited answer, and it is precisely the one that
    survives a token-overlap test -- "revenue rose 12%" and "revenue rose 21%"
    are lexically almost identical.
    """
    out: Set[str] = set()
    for token in tokenize(text):
        if not _NUMERIC_RE.match(token):
            continue
        normalized = token.replace(",", "").rstrip("%")
        # Trailing zeros after a decimal point are not a different number.
        if "." in normalized:
            normalized = normalized.rstrip("0").rstrip(".")
        if normalized:
            out.add(normalized)
    return out


def normalize_for_match(text: str) -> str:
    """Lowercase, alphanumerics only -- defeats spacing and punctuation tricks.

    Used for canary detection, where an exfiltrated secret may come back as
    ``C-A-N-A-R-Y`` or ``c a n a r y``. It will not catch an encoded leak
    (base64, rot13, acrostic); that limitation is stated in the probe.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


def contains_normalized(haystack: str, needle: str) -> bool:
    """Substring test after :func:`normalize_for_match` on both sides."""
    needle_norm = normalize_for_match(needle)
    if not needle_norm:
        return False
    return needle_norm in normalize_for_match(haystack)


def any_contains(haystack: str, needles: Iterable[str]) -> bool:
    """True if any needle appears, case-insensitively, in ``haystack``."""
    hay = haystack.lower()
    return any(n.lower() in hay for n in needles if n)


def cluster_by_similarity(
    texts: Sequence[str], threshold: float
) -> List[List[int]]:
    """Greedy single-representative clustering of ``texts`` by token Jaccard.

    Each text joins the first existing cluster whose *representative* (its first
    member) is at least ``threshold`` similar; otherwise it starts a cluster.

    Greedy and order-dependent, which is a real limitation: a different
    presentation order can produce a different partition. It is used anyway
    because it is deterministic, needs no model, and the quantity that matters
    downstream -- the size of the largest cluster -- is stable in the cases that
    matter (answers that clearly agree, answers that clearly do not). Probes
    report the cluster count so a reviewer can see when the partition is
    borderline.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be in [0, 1], got {threshold!r}")
    token_sets = [content_tokens(t) for t in texts]
    clusters: List[List[int]] = []
    for index, tokens in enumerate(token_sets):
        for cluster in clusters:
            if jaccard(tokens, token_sets[cluster[0]]) >= threshold:
                cluster.append(index)
                break
        else:
            clusters.append([index])
    return clusters
