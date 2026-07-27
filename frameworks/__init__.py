"""Framework control catalogs and coverage mapping.

Control identifiers plus original one-line summaries only -- no framework text
is reproduced (D-003). A mapping means a procedure produced evidence relevant
to a control, never that the control is satisfied.
"""

from frameworks.catalog import (  # noqa: F401
    CAPABILITY_DRIFT_MONITORING,
    CAPABILITY_EVIDENCE_JOURNAL,
    CAPABILITY_WORKPAPERS,
    MAX_SUMMARY_CHARS,
    Control,
    ControlReference,
    Framework,
    MappingSet,
    capability_source,
    load_frameworks,
    load_mappings,
    probe_source,
)
from frameworks.coverage import (  # noqa: F401
    STATUS_EVIDENCE_PRESENT,
    STATUS_NO_EVIDENCE,
    STATUS_TESTED_ERROR,
    STATUS_TESTED_EXCEPTIONS,
    STATUS_TESTED_INCONCLUSIVE,
    STATUS_TESTED_PASS,
    ControlCoverage,
    CoverageReport,
    FrameworkCoverage,
    build_coverage,
)

__all__ = [
    "CAPABILITY_DRIFT_MONITORING",
    "CAPABILITY_EVIDENCE_JOURNAL",
    "CAPABILITY_WORKPAPERS",
    "MAX_SUMMARY_CHARS",
    "Control",
    "ControlCoverage",
    "ControlReference",
    "CoverageReport",
    "Framework",
    "FrameworkCoverage",
    "MappingSet",
    "STATUS_EVIDENCE_PRESENT",
    "STATUS_NO_EVIDENCE",
    "STATUS_TESTED_ERROR",
    "STATUS_TESTED_EXCEPTIONS",
    "STATUS_TESTED_INCONCLUSIVE",
    "STATUS_TESTED_PASS",
    "build_coverage",
    "capability_source",
    "load_frameworks",
    "load_mappings",
    "probe_source",
]
