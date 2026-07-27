"""Running one battery against several endpoints and comparing the results.

No overall ranking is produced -- see ``compare.matrix`` for why.
"""

from compare.matrix import (  # noqa: F401
    ComparisonMatrix,
    EndpointRun,
    MetricRow,
    run_comparison,
)

__all__ = ["ComparisonMatrix", "EndpointRun", "MetricRow", "run_comparison"]
