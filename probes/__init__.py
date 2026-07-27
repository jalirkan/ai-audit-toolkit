"""Test procedures. Each returns evidence, never a verdict.

Importing this package registers every built-in probe, so ``PROBES`` is
populated for battery configs and control mappings that name a probe by id.
"""

from probes.base import (  # noqa: F401
    PROBES,
    Decision,
    Probe,
    available_probes,
    decide,
    get_probe,
)
from probes.citation import CitationCase, CitationFaithfulnessProbe  # noqa: F401
from probes.consistency import ConsistencyCase, ConsistencyProbe  # noqa: F401
from probes.injection import (  # noqa: F401
    InjectionResistanceProbe,
    InjectionScenario,
)

__all__ = [
    "PROBES",
    "Decision",
    "Probe",
    "available_probes",
    "decide",
    "get_probe",
    "CitationCase",
    "CitationFaithfulnessProbe",
    "ConsistencyCase",
    "ConsistencyProbe",
    "InjectionResistanceProbe",
    "InjectionScenario",
]
