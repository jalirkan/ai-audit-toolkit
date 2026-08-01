"""Regenerate the front-end test fixtures from real engine output.

    python web/tests/fixtures/generate.py

Every file beside this one is written by this script and nothing else. They are
the API's actual responses, captured through :class:`serve.AuditApi` rather than
hand-written -- the same reason ``examples/generate.py`` loads the shipped
fixture instead of restating its numbers. A hand-written fixture is a second
description of the payload shape, and it drifts from the first silently, which
is precisely the failure the front-end tests exist to catch.

The engine workspace is a temporary directory, so regenerating fixtures never
touches ``runs/``, ``baselines/``, or the real journal. Only the captured JSON
lands in the repository.

## What is generated, and why each one

The set is chosen so the views can be tested against every state the engine can
report, not just the happy one:

- ``demo-vendor-assistant`` produces a fail, a pass, **and** an inconclusive
  unit in a single run (D-034), so one fixture exercises all three treatments.
- The bare mock is run as a second endpoint. It does not know a fictional
  shipping policy, so its outcomes differ -- which is what gives the comparison
  fixture genuinely overlapping and genuinely separated intervals rather than
  a contrived pair.
- The journal is real: entries are appended by the runs above, so the chain
  verifies and the head is a true head.

``started_at`` is pinned so run ids stay stable across regenerations and the
fixture filenames do not churn. ``finished_at`` is whatever the clock said; it
is left alone because the payload is meant to be real engine output, and a
doctored timestamp in an audit fixture would be an odd lesson to encode.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT))

from adapters.mock import MockAdapter, load_mock_script  # noqa: E402
from battery.runner import run_battery  # noqa: E402
from battery.spec import BatterySpec  # noqa: E402
from drift.baseline import BaselineStore  # noqa: E402
from journal.store import Journal  # noqa: E402
from serve import AuditApi  # noqa: E402

#: Pinned so run ids are reproducible. Any fixed instant would do.
BASELINE_STARTED_AT = "2026-07-31T09:00:00.000000Z"
CANDIDATE_STARTED_AT = "2026-07-31T09:30:00.000000Z"

BASELINE_LABEL = "q3-2026"


def _write(name: str, payload: object) -> Path:
    path = HERE / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def build(workspace: Path) -> AuditApi:
    """Run two batteries into a scratch workspace and return an API over it."""
    shutil.copytree(REPO_ROOT / "suites", workspace / "suites")
    shutil.copytree(REPO_ROOT / "datasets", workspace / "datasets")

    spec = BatterySpec.load(workspace / "suites" / "baseline.json")
    journal_path = workspace / "runs" / "journal.db"

    # The fixture file names its own model, so the adapter arrives complete.
    scripted = load_mock_script(workspace / "suites" / "demo-endpoint.json")
    # The bare mock is a hash-echo: it does not know the fictional policy, so
    # it fails units the scripted fixture passes. That difference is the point.
    bare = MockAdapter(model="bare-mock")

    runs_dir = workspace / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    with Journal(journal_path) as jrnl:
        scripted_result = run_battery(
            spec, scripted, journal=jrnl, started_at=BASELINE_STARTED_AT
        )
        bare_result = run_battery(
            spec, bare, journal=jrnl, started_at=CANDIDATE_STARTED_AT
        )

    for result in (scripted_result, bare_result):
        (runs_dir / f"{result.run_id}.json").write_text(
            json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8"
        )

    BaselineStore(workspace / "baselines").save(
        BASELINE_LABEL,
        scripted_result,
        note="Scripted demo endpoint, captured as the reference run.",
    )

    shutil.copy(REPO_ROOT / "DECISIONS.md", workspace / "DECISIONS.md")
    return AuditApi(root=workspace)


def capture(api: AuditApi) -> list[Path]:
    written: list[Path] = []
    runs = api.runs()
    # Identify runs by what they tested, not by their position in the index --
    # the index is sorted newest-first, so positional labelling silently swaps
    # the endpoints and mislabels every interval in the comparison fixture.
    by_model = {r["fingerprint"]["model"]: r["run_id"] for r in runs}
    scripted_id = by_model["demo-vendor-assistant"]
    bare_id = by_model["bare-mock"]
    run_ids = [r["run_id"] for r in runs]

    written.append(_write("meta.json", api.meta()))
    written.append(_write("runs-index.json", runs))
    written.append(_write("probes.json", api.probes()))
    written.append(_write("suites.json", api.suites()))
    written.append(_write("baselines.json", api.baselines()))

    for run_id in run_ids:
        written.append(_write(f"run-{run_id}.json", api.run(run_id)))
        written.append(
            _write(
                f"workpaper-{run_id}.json",
                api.workpaper(run_id, ("evidence-journal", "workpapers")),
            )
        )
        written.append(
            _write(
                f"coverage-{run_id}.json",
                api.coverage(
                    run_id,
                    ("evidence-journal", "drift-monitoring", "workpapers"),
                ),
            )
        )

    written.append(
        _write("journal-entries.json", api.journal_entries(limit=50))
    )
    written.append(_write("journal-verify.json", api.journal_verify()))
    written.append(
        _write(
            "journal-verify-anchored.json",
            api.journal_verify(api.journal_entries(limit=1)["head"]),
        )
    )
    # The baseline is the scripted run, so comparing the bare mock against it
    # produces genuine drift -- outcomes that worsened and rates that moved.
    written.append(_write("drift.json", api.drift(BASELINE_LABEL, bare_id)))
    # ...and comparing the scripted run against itself produces a clean one, so
    # the "no drift" state is testable too.
    written.append(
        _write("drift-clean.json", api.drift(BASELINE_LABEL, scripted_id))
    )
    written.append(
        _write(
            "comparison.json",
            api.comparison((scripted_id, bare_id), ("scripted", "bare-mock")),
        )
    )
    written.append(
        _write(
            "rag-screen-check.json",
            api.rag_screen_check("northwind-rag-golden.json"),
        )
    )
    return written


def main() -> int:
    with TemporaryDirectory() as tmp:
        api = build(Path(tmp))
        written = capture(api)
    for path in written:
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    print(f"\n{len(written)} fixture(s) regenerated from live engine output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
