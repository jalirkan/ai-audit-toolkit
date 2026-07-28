"""Golden-dataset RAG faithfulness harness.

This is not a retrieval engine and not a second faithfulness metric. The
dataset supplies closed-context sources (auditor-chosen "retrieved" chunks) and
planted gold answers; the citation screen scores them. See DECISIONS D-038.
"""

from rag.dataset import EXPECT_FAITHFUL, EXPECT_UNFAITHFUL, GoldenDataset, GoldenItem
from rag.harness import ScreenCheckResult, run_live, run_screen_check

__all__ = [
    "EXPECT_FAITHFUL",
    "EXPECT_UNFAITHFUL",
    "GoldenDataset",
    "GoldenItem",
    "ScreenCheckResult",
    "run_screen_check",
    "run_live",
]
