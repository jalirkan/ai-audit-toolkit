#!/usr/bin/env python3
"""Regenerate the committed example artifacts.

    python examples/generate.py

The example is a fictional vendor endpoint audited twice. Version 1 becomes the
baseline; version 2 has regressed, and the toolkit is asked to say so. Both
versions are scripted mocks, so this runs offline and produces the same
artifacts every time.

The scripted weaknesses are chosen to exercise all three outcomes, because a
sample report where everything passes teaches the reader nothing about how the
tool behaves when it does not:

- **injection-resistance** fails -- two attacks retrieve the canary.
- **output-consistency** passes -- the delivery-window answer is stable.
- **citation-faithfulness** is inconclusive -- two answers of twenty contain an
  unsupported figure, and the interval for that rate straddles the tolerance.
  The sample cannot settle the question, and the report says so rather than
  rounding it to a pass.
"""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from adapters.mock import (  # noqa: E402
    MATCH_ANY,
    MockAdapter,
    MockRule,
    load_mock_script,
)
from battery.runner import run_battery  # noqa: E402
from battery.spec import BatterySpec  # noqa: E402
from drift.compare import compare_runs  # noqa: E402
from frameworks.catalog import (  # noqa: E402
    CAPABILITY_EVIDENCE_JOURNAL,
    CAPABILITY_WORKPAPERS,
)
from frameworks.coverage import build_coverage  # noqa: E402
from journal.store import Journal  # noqa: E402
from probes.injection import DEFAULT_CANARY  # noqa: E402
from report.document import render_html, render_markdown  # noqa: E402
from report.letter import build_letter  # noqa: E402
from report.workpaper import build_workpapers  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent
SUITE = REPO_ROOT / "suites" / "baseline.json"

REFUSAL = (
    "I can't share that. Is there something about your shipment I can help with?"
)
DELIVERY_ANSWER = (
    "Standard delivery is quoted at five to seven business days within the "
    "contiguous United States."
)
ABSTENTION = "The sources do not contain that information."

# Answers that stay inside the source set.
SUPPORTED = {
    "service tiers does Northwind offer": (
        "Northwind Logistics offers three service tiers: Standard, Priority, "
        "and Overnight."
    ),
    "How long does Standard delivery take": DELIVERY_ANSWER,
    "How long does Priority delivery take": (
        "Priority delivery is quoted at two to three business days."
    ),
    "surcharge applies to Priority": (
        "Priority delivery carries a surcharge of 18 dollars per shipment."
    ),
    "Where is Overnight delivery available": (
        "Overnight delivery is available only to addresses within 50 miles of a "
        "Northwind hub."
    ),
    "file a damage claim": (
        "Claims for damaged goods must be filed within 14 days of delivery."
    ),
    "ship live animals": (
        "Northwind does not ship live animals or hazardous materials under any "
        "service tier."
    ),
}

# Version one fabricates on one question; version two, defined in
# suites/demo-endpoint.json, adds a second and starts leaking the canary.
FABRICATED_V1 = {
    "same-day delivery": (
        "Northwind offers same-day delivery in 12 metropolitan areas for an "
        "additional 40 dollars."
    ),
}


def _citation_rules(fabrications) -> list:
    rules = [
        MockRule.make(pattern=key, responses=value)
        for key, value in fabrications.items()
    ]
    rules += [
        MockRule.make(pattern=key, responses=value)
        for key, value in SUPPORTED.items()
    ]
    # Any other question against the source set: decline rather than guess.
    rules.append(MockRule.make(pattern="Sources:", responses=ABSTENTION))
    return rules


def version_one() -> MockAdapter:
    """The endpoint as first assessed: holds the canary, mostly faithful."""
    return MockAdapter(
        _citation_rules(FABRICATED_V1)
        + [MockRule.make(responses=DELIVERY_ANSWER, mode=MATCH_ANY)],
        model="demo-vendor-assistant-v1",
    )


def version_two() -> MockAdapter:
    """After a model change: leaks the canary and fabricates more.

    Loaded from the shipped fixture rather than rebuilt here, so the endpoint
    behind the README quick start and the one behind these artifacts cannot
    drift apart.
    """
    return load_mock_script(REPO_ROOT / "suites" / "demo-endpoint.json")


def main() -> int:
    spec = BatterySpec.load(SUITE)

    with TemporaryDirectory() as tmp:
        journal = Journal(Path(tmp) / "journal.db")

        # Timestamps come from the clock rather than being pinned. Pinning the
        # start without the finish produced a header showing a run that ended
        # before it began, and pinning only the outer pair would leave the
        # per-workpaper times outside the run window. Coherent timestamps are
        # worth more here than stable run ids: these artifacts are a snapshot,
        # and both change when the script is re-run.
        baseline = run_battery(spec, version_one())
        current = run_battery(spec, version_two(), journal=journal)

        journal_head = journal.head()
        verification = journal.verify()
        journal_lines = [
            "$ python cli.py journal show --limit 4",
            "",
        ]
        for entry in journal.entries()[-4:]:
            payload = entry.parsed()
            detail = (
                f"{payload.get('probe_id')} [{payload.get('outcome')}]"
                if entry.kind == "evidence"
                else f"{payload.get('battery')} run {payload.get('run_id')}"
            )
            journal_lines.append(
                f"{entry.seq:>5}  {entry.recorded_at}  {entry.kind:<8}  {detail}"
            )
        journal_lines += [
            "",
            f"Head: {journal_head}",
            "",
            "$ python cli.py journal verify",
            "",
            verification.summary(),
            "",
            "Note: this confirms the chain is internally consistent. It does not",
            "rule out a wholesale rebuild. Re-run with --expect-head <hash>",
            "against a head recorded elsewhere.",
        ]
        journal.close()

    coverage = build_coverage(
        current, capabilities=[CAPABILITY_EVIDENCE_JOURNAL, CAPABILITY_WORKPAPERS]
    )
    workpapers = build_workpapers(
        current, journal_head=journal_head, prepared_by="J. Alirkan"
    )
    letter = build_letter(
        current,
        coverage=coverage,
        addressee="Vendor Management",
        prepared_by="J. Alirkan",
    )
    drift = compare_runs(baseline, current, baseline_label="v1-preupgrade")

    artifacts = {
        "workpapers.md": render_markdown(workpapers),
        "management-letter.md": render_markdown(letter),
        "management-letter.html": render_html(letter),
        "journal.txt": "\n".join(journal_lines) + "\n",
        "drift.txt": (
            "$ python cli.py drift suites/baseline.json --baseline v1-preupgrade\n\n"
            + "\n".join(drift.summary_lines())
            + "\n"
        ),
        "coverage.txt": (
            "$ python cli.py coverage suites/baseline.json --all-capabilities\n\n"
            + "\n".join(coverage.summary_lines())
            + "\n"
        ),
    }

    for name, content in artifacts.items():
        (OUT_DIR / name).write_text(content, encoding="utf-8")
        print(f"wrote examples/{name}")

    print()
    print("\n".join(current.summary_lines()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
