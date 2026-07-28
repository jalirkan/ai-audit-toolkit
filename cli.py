#!/usr/bin/env python3
"""Command line for the AI audit toolkit.

    python cli.py run suites/baseline.json
    python cli.py journal verify
    python cli.py report <run-id>
    python cli.py baseline save <run-id> pre-upgrade
    python cli.py drift suites/baseline.json --baseline pre-upgrade
    python cli.py monitor suites/baseline.json --baseline pre-upgrade
    python cli.py rag datasets/northwind-rag-golden.json --screen-only
    python cli.py probes
    python cli.py coverage suites/baseline.json

Everything defaults to the offline mock adapter. Reaching a real endpoint takes
both ``--adapter`` and the matching key in the environment, and there is no
fallback in either direction: asking for a real adapter without a key is an
error rather than a quiet downgrade to the mock, because evidence must never
carry the name of an endpoint it did not come from.

Runs are written to ``runs/<run-id>.json`` so that ``report`` and ``baseline
save`` can work from a completed run without re-querying the model -- which
matters when the model costs money, and matters more when re-running would
produce different answers.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from adapters.mock import load_mock_script
from adapters.remote import ADAPTER_MOCK, ADAPTER_NAMES, build_adapter
from battery.runner import BatteryResult, run_battery
from battery.spec import BatterySpec
from compare.matrix import run_comparison
from core.evidence import OUTCOME_FAIL, OUTCOME_PASS, utc_now_iso
from drift.baseline import BaselineStore
from drift.compare import compare_runs
from frameworks.catalog import (
    CAPABILITY_DRIFT_MONITORING,
    CAPABILITY_EVIDENCE_JOURNAL,
    CAPABILITY_WORKPAPERS,
)
from frameworks.coverage import build_coverage
from journal.store import Journal
from probes.base import PROBES, available_probes
from rag.dataset import load_dataset
from rag.harness import run_live, run_screen_check
from report.comparison import build_comparison_report
from report.document import render_html, render_markdown
from report.letter import build_letter
from report.workpaper import build_workpapers

DEFAULT_RUNS_DIR = "runs"
DEFAULT_JOURNAL = "runs/journal.db"
DEFAULT_BASELINES_DIR = "baselines"
DEFAULT_MONITOR_STATUS = "runs/monitor-status.json"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_BROKEN_CHAIN = 3
EXIT_DRIFT = 4

__all__ = ["main", "build_parser"]


# --- helpers -----------------------------------------------------------------


def _save_run(result: BatteryResult, runs_dir: str) -> Path:
    directory = Path(runs_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result.run_id}.json"
    path.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def _load_run(run_id: str, runs_dir: str) -> BatteryResult:
    path = Path(runs_dir) / f"{run_id}.json"
    if not path.exists():
        available = sorted(p.stem for p in Path(runs_dir).glob("*.json"))
        raise SystemExit(
            f"no stored run {run_id!r} in {runs_dir}/. "
            f"Available: {available if available else 'none'}"
        )
    return BatteryResult.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _write_reports(
    result: BatteryResult,
    out_dir: str,
    formats: Sequence[str],
    *,
    journal_head: str = "",
    prepared_by: str = "",
) -> List[Path]:
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    coverage = build_coverage(
        result,
        capabilities=[CAPABILITY_EVIDENCE_JOURNAL, CAPABILITY_WORKPAPERS]
        if journal_head
        else [CAPABILITY_WORKPAPERS],
    )
    documents = {
        "workpapers": build_workpapers(
            result, journal_head=journal_head, prepared_by=prepared_by
        ),
        "management-letter": build_letter(
            result, coverage=coverage, prepared_by=prepared_by
        ),
    }
    written: List[Path] = []
    for stem, document in documents.items():
        if "md" in formats:
            path = directory / f"{result.run_id}-{stem}.md"
            path.write_text(render_markdown(document), encoding="utf-8")
            written.append(path)
        if "html" in formats:
            path = directory / f"{result.run_id}-{stem}.html"
            path.write_text(render_html(document), encoding="utf-8")
            written.append(path)
    return written


def _adapter_from_args(args: argparse.Namespace):
    script = getattr(args, "mock_script", None)
    if script:
        if args.adapter != ADAPTER_MOCK:
            raise SystemExit(
                "--mock-script only applies to the mock adapter; it cannot be "
                f"combined with --adapter {args.adapter}"
            )
        try:
            return load_mock_script(script)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"could not load mock script {script}: {exc}")
    try:
        return build_adapter(args.adapter, model=args.model)
    except ValueError as exc:
        raise SystemExit(str(exc))


def _load_spec(path: str) -> BatterySpec:
    try:
        return BatterySpec.load(path)
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc))


# --- commands ----------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    spec = _load_spec(args.suite)
    adapter = _adapter_from_args(args)

    journal: Optional[Journal] = None
    if not args.no_journal:
        journal = Journal(args.journal)

    print(f"Running {spec.name} against {adapter.describe()}")
    try:
        result = run_battery(spec, adapter, journal=journal)
        head = journal.head() if journal else ""
        if journal:
            verification = journal.verify()
            print(f"Journal: {verification.summary()}")
    finally:
        if journal:
            journal.close()

    print()
    print("\n".join(result.summary_lines()))

    run_path = _save_run(result, args.runs_dir)
    print(f"\nRun stored at {run_path}")

    if args.report:
        written = _write_reports(
            result, args.out, args.format, journal_head=head, prepared_by=args.prepared_by
        )
        for path in written:
            print(f"Wrote {path}")

    if args.baseline:
        store = BaselineStore(args.baselines_dir)
        try:
            path = store.save(
                args.baseline, result, note=args.note, overwrite=args.overwrite_baseline
            )
            print(f"Baseline {args.baseline!r} saved to {path}")
        except FileExistsError as exc:
            raise SystemExit(str(exc))

    return EXIT_FINDINGS if result.outcome == OUTCOME_FAIL else EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    result = _load_run(args.run_id, args.runs_dir)
    written = _write_reports(
        result, args.out, args.format, prepared_by=args.prepared_by
    )
    for path in written:
        print(f"Wrote {path}")
    return EXIT_OK


def cmd_journal_show(args: argparse.Namespace) -> int:
    journal = Journal(args.journal)
    try:
        entries = journal.entries()
        if args.limit:
            entries = entries[-args.limit :]
        if not entries:
            print(f"{args.journal} is empty.")
            return EXIT_OK
        for entry in entries:
            payload = entry.parsed()
            if entry.kind == "evidence":
                detail = (
                    f"{payload.get('probe_id')} "
                    f"[{payload.get('outcome')}] "
                    f"unit={payload.get('config', {}).get('unit', '-')}"
                )
            elif entry.kind == "run":
                detail = (
                    f"{payload.get('battery')} run {payload.get('run_id')} "
                    f"[{payload.get('outcome')}]"
                )
            else:
                detail = str(payload)[:80]
            print(f"{entry.seq:>5}  {entry.recorded_at}  {entry.kind:<8}  {detail}")
        print(f"\nHead: {journal.head()}")
        print(
            "Record the head hash somewhere outside this database. The chain "
            "proves history was not edited; only an external anchor shows it "
            "was not rebuilt."
        )
    finally:
        journal.close()
    return EXIT_OK


def cmd_journal_verify(args: argparse.Namespace) -> int:
    journal = Journal(args.journal)
    try:
        result = journal.verify(expected_head=args.expect_head)
        print(result.summary())
        for problem in result.problems:
            print(f"  {problem}")
        if result.ok and not args.expect_head:
            print(
                "\nNote: this confirms the chain is internally consistent. It "
                "does not rule out a wholesale rebuild. Re-run with "
                "--expect-head <hash> against a head recorded elsewhere."
            )
    finally:
        journal.close()
    return EXIT_OK if result.ok else EXIT_BROKEN_CHAIN


def cmd_drift(args: argparse.Namespace) -> int:
    spec = _load_spec(args.suite)
    adapter = _adapter_from_args(args)
    store = BaselineStore(args.baselines_dir)
    try:
        baseline = store.load(args.baseline)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc))

    journal: Optional[Journal] = None
    if not args.no_journal:
        journal = Journal(args.journal)
    try:
        current = run_battery(spec, adapter, journal=journal)
    finally:
        if journal:
            journal.close()

    _save_run(current, args.runs_dir)
    report = compare_runs(
        baseline.result,
        current,
        baseline_label=args.baseline,
        resamples=args.resamples,
        seed=args.seed,
    )
    print("\n".join(report.summary_lines()))
    return EXIT_DRIFT if report.has_drift else EXIT_OK


def _write_monitor_status(path: str, payload: Dict[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def cmd_monitor(args: argparse.Namespace) -> int:
    """Re-run a suite against a baseline and write a machine-readable status.

    Intended for cron / Task Scheduler: exit non-zero on drift; scrape
    ``--status-out`` rather than stdout if you need structured detail.
    """
    spec = _load_spec(args.suite)
    adapter = _adapter_from_args(args)
    store = BaselineStore(args.baselines_dir)
    try:
        baseline = store.load(args.baseline)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc))

    journal: Optional[Journal] = None
    if not args.no_journal:
        journal = Journal(args.journal)
    try:
        current = run_battery(spec, adapter, journal=journal)
    finally:
        if journal:
            journal.close()

    _save_run(current, args.runs_dir)
    report = compare_runs(
        baseline.result,
        current,
        baseline_label=args.baseline,
        resamples=args.resamples,
        seed=args.seed,
    )
    has_drift = report.has_drift
    exit_code = EXIT_DRIFT if has_drift else EXIT_OK
    status = {
        "checked_at": utc_now_iso(),
        "suite": args.suite,
        "baseline": args.baseline,
        "run_id": current.run_id,
        "has_drift": has_drift,
        "exit_code": exit_code,
        "exit_reason": "drift-detected" if has_drift else "no-drift",
        "summary": report.summary_lines(),
        "report": report.to_dict(),
    }
    status_path = _write_monitor_status(args.status_out, status)
    print("\n".join(report.summary_lines()))
    print(f"Monitor status written to {status_path}")
    return exit_code


def cmd_rag(args: argparse.Namespace) -> int:
    try:
        dataset = load_dataset(args.dataset)
    except ValueError as exc:
        raise SystemExit(str(exc))

    if args.screen_only:
        result = run_screen_check(
            dataset,
            min_accuracy=args.min_accuracy,
            min_sample=args.min_sample,
        )
        print("\n".join(result.summary_lines()))
        if args.status_out:
            path = _write_monitor_status(args.status_out, result.to_dict())
            print(f"Screen-check result written to {path}")
        if result.outcome == OUTCOME_PASS:
            return EXIT_OK
        if result.outcome == OUTCOME_FAIL:
            return EXIT_FINDINGS
        return EXIT_OK  # inconclusive: ran cleanly, did not establish a finding

    adapter = _adapter_from_args(args)
    evidence = run_live(
        dataset,
        adapter,
        max_unsupported_answer_rate=args.max_unsupported_answer_rate,
        min_sample=args.min_sample,
    )
    failed = False
    for record in evidence:
        print(record.summary())
        if record.outcome == OUTCOME_FAIL:
            failed = True
    return EXIT_FINDINGS if failed else EXIT_OK


def cmd_baseline_save(args: argparse.Namespace) -> int:
    result = _load_run(args.run_id, args.runs_dir)
    store = BaselineStore(args.baselines_dir)
    try:
        path = store.save(
            args.label, result, note=args.note, overwrite=args.overwrite
        )
    except (FileExistsError, ValueError) as exc:
        raise SystemExit(str(exc))
    print(f"Baseline {args.label!r} saved to {path}")
    return EXIT_OK


def cmd_baseline_list(args: argparse.Namespace) -> int:
    store = BaselineStore(args.baselines_dir)
    labels = store.labels()
    if not labels:
        print(f"No baselines in {args.baselines_dir}/.")
        return EXIT_OK
    for label in labels:
        baseline = store.load(label)
        print(
            f"{label:<24} {baseline.result.battery:<24} "
            f"run {baseline.result.run_id}  saved {baseline.saved_at}"
        )
        if baseline.note:
            print(f"{'':<24} {baseline.note}")
    return EXIT_OK


def _parse_endpoint(spec: str):
    """Parse ``label=kind[:detail]`` into ``(label, adapter)``.

    Accepted forms::

        prod=mock:suites/demo-endpoint.json    scripted offline fixture
        baseline=mock                          the bare deterministic mock
        vendor=anthropic:claude-sonnet-4-5     real endpoint, needs its key
        local=openai:llama-3-8b                OpenAI-compatible endpoint
    """
    if "=" not in spec:
        raise SystemExit(
            f"endpoint {spec!r} must be written label=kind[:detail], "
            "e.g. prod=mock:suites/demo-endpoint.json"
        )
    label, _, target = spec.partition("=")
    if not label:
        raise SystemExit(f"endpoint {spec!r} has an empty label")
    kind, _, detail = target.partition(":")

    if kind == ADAPTER_MOCK:
        if detail:
            try:
                return label, load_mock_script(detail)
            except (OSError, ValueError) as exc:
                raise SystemExit(f"endpoint {label!r}: {exc}")
        return label, build_adapter(ADAPTER_MOCK)
    if kind in ADAPTER_NAMES:
        try:
            return label, build_adapter(kind, model=detail or None)
        except ValueError as exc:
            raise SystemExit(f"endpoint {label!r}: {exc}")
    raise SystemExit(
        f"endpoint {label!r}: unknown kind {kind!r}; expected one of "
        f"{list(ADAPTER_NAMES)}"
    )


def cmd_compare(args: argparse.Namespace) -> int:
    spec = _load_spec(args.suite)
    endpoints = [_parse_endpoint(e) for e in args.endpoint]

    journal: Optional[Journal] = None
    if not args.no_journal:
        journal = Journal(args.journal)
    try:
        matrix = run_comparison(spec, endpoints, journal=journal)
    finally:
        if journal:
            journal.close()

    print("\n".join(matrix.summary_lines()))

    if args.out:
        directory = Path(args.out)
        directory.mkdir(parents=True, exist_ok=True)
        document = build_comparison_report(matrix, prepared_by=args.prepared_by)
        for fmt, render in (("md", render_markdown), ("html", render_html)):
            if fmt in args.format:
                path = directory / f"comparison-{spec.name}.{fmt}"
                path.write_text(render(document), encoding="utf-8")
                print(f"\nWrote {path}")

    return (
        EXIT_FINDINGS
        if any(e.result.outcome == OUTCOME_FAIL for e in matrix.endpoints)
        else EXIT_OK
    )


def cmd_probes(args: argparse.Namespace) -> int:
    for probe_id in available_probes():
        probe_cls = PROBES[probe_id]
        print(f"{probe_id}\n    {probe_cls.title}")
        if args.verbose:
            print(f"    procedure:   {probe_cls.procedure}")
            print(f"    limitations: {probe_cls.limitations}")
        print()
    return EXIT_OK


def cmd_coverage(args: argparse.Namespace) -> int:
    spec = _load_spec(args.suite)
    report = build_coverage(
        probe_ids=spec.probe_ids,
        capabilities=[
            CAPABILITY_EVIDENCE_JOURNAL,
            CAPABILITY_DRIFT_MONITORING,
            CAPABILITY_WORKPAPERS,
        ]
        if args.all_capabilities
        else [],
    )
    print("\n".join(report.summary_lines()))
    print(
        "\nThis is projected coverage for the suite as configured, before "
        "running it. It says which controls the procedures would speak to, not "
        "what they would find."
    )
    return EXIT_OK


# --- parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-audit-toolkit",
        description=(
            "Repeatable assurance procedures against LLM endpoints, with a "
            "tamper-evident evidence journal and framework-mapped workpapers. "
            "Runs fully offline against the mock adapter by default."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_adapter_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--adapter",
            default=ADAPTER_MOCK,
            choices=list(ADAPTER_NAMES),
            help="endpoint to test (default: the offline mock)",
        )
        p.add_argument("--model", default=None, help="model identifier")
        p.add_argument(
            "--mock-script",
            default=None,
            help=(
                "JSON fixture defining a scripted mock endpoint "
                "(e.g. suites/demo-endpoint.json); mock adapter only"
            ),
        )

    def add_journal_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--journal", default=DEFAULT_JOURNAL, help="journal database")
        p.add_argument(
            "--no-journal",
            action="store_true",
            help="run without recording evidence (not recommended)",
        )

    def add_store_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
        p.add_argument("--baselines-dir", default=DEFAULT_BASELINES_DIR)

    def add_report_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--out", default=DEFAULT_RUNS_DIR, help="report output dir")
        p.add_argument(
            "--format",
            nargs="+",
            default=["md"],
            choices=["md", "html"],
            help="report formats to write",
        )
        p.add_argument("--prepared-by", default="", help="name for the report header")

    run = subparsers.add_parser("run", help="run a battery")
    run.add_argument("suite", help="path to a battery spec JSON file")
    add_adapter_args(run)
    add_journal_args(run)
    add_store_args(run)
    add_report_args(run)
    run.add_argument("--report", action="store_true", help="also render reports")
    run.add_argument("--baseline", default="", help="save this run under a label")
    run.add_argument("--note", default="", help="note stored with the baseline")
    run.add_argument("--overwrite-baseline", action="store_true")
    run.set_defaults(func=cmd_run)

    report = subparsers.add_parser("report", help="render reports for a stored run")
    report.add_argument("run_id")
    add_store_args(report)
    add_report_args(report)
    report.set_defaults(func=cmd_report)

    journal = subparsers.add_parser("journal", help="inspect the evidence journal")
    journal_sub = journal.add_subparsers(dest="journal_command", required=True)

    show = journal_sub.add_parser("show", help="list journal entries")
    show.add_argument("--journal", default=DEFAULT_JOURNAL)
    show.add_argument("--limit", type=int, default=0, help="show only the last N")
    show.set_defaults(func=cmd_journal_show)

    verify = journal_sub.add_parser("verify", help="verify the hash chain")
    verify.add_argument("--journal", default=DEFAULT_JOURNAL)
    verify.add_argument(
        "--expect-head",
        default=None,
        help="head hash recorded elsewhere, to detect a rebuilt journal",
    )
    verify.set_defaults(func=cmd_journal_verify)

    drift = subparsers.add_parser("drift", help="re-run and compare to a baseline")
    drift.add_argument("suite")
    drift.add_argument("--baseline", required=True, help="baseline label")
    add_adapter_args(drift)
    add_journal_args(drift)
    add_store_args(drift)
    drift.add_argument("--resamples", type=int, default=10000)
    drift.add_argument("--seed", type=int, default=20260727)
    drift.set_defaults(func=cmd_drift)

    monitor = subparsers.add_parser(
        "monitor",
        help="re-run vs a baseline and write a status file (for cron)",
    )
    monitor.add_argument("suite")
    monitor.add_argument("--baseline", required=True, help="baseline label")
    add_adapter_args(monitor)
    add_journal_args(monitor)
    add_store_args(monitor)
    monitor.add_argument("--resamples", type=int, default=10000)
    monitor.add_argument("--seed", type=int, default=20260727)
    monitor.add_argument(
        "--status-out",
        default=DEFAULT_MONITOR_STATUS,
        help="path for the machine-readable status JSON",
    )
    monitor.set_defaults(func=cmd_monitor)

    rag = subparsers.add_parser(
        "rag",
        help="run a golden RAG faithfulness dataset (screen-check or live)",
    )
    rag.add_argument("dataset", help="path to a golden dataset JSON file")
    rag.add_argument(
        "--screen-only",
        action="store_true",
        help="score gold answers with the citation screen; no model calls",
    )
    rag.add_argument(
        "--min-accuracy",
        type=float,
        default=0.9,
        help="minimum screen accuracy for a pass (screen-only)",
    )
    rag.add_argument(
        "--min-sample",
        type=int,
        default=20,
        help="minimum items before a pass may be concluded",
    )
    rag.add_argument(
        "--max-unsupported-answer-rate",
        type=float,
        default=0.2,
        help="tolerance for the live citation probe",
    )
    rag.add_argument(
        "--status-out",
        default="",
        help="optional path to write the screen-check result JSON",
    )
    add_adapter_args(rag)
    rag.set_defaults(func=cmd_rag)

    baseline = subparsers.add_parser("baseline", help="manage baselines")
    baseline_sub = baseline.add_subparsers(dest="baseline_command", required=True)

    save = baseline_sub.add_parser("save", help="label a stored run as a baseline")
    save.add_argument("run_id")
    save.add_argument("label")
    save.add_argument("--note", default="")
    save.add_argument("--overwrite", action="store_true")
    add_store_args(save)
    save.set_defaults(func=cmd_baseline_save)

    listing = baseline_sub.add_parser("list", help="list saved baselines")
    add_store_args(listing)
    listing.set_defaults(func=cmd_baseline_list)

    compare = subparsers.add_parser(
        "compare", help="run one battery against several endpoints"
    )
    compare.add_argument("suite")
    compare.add_argument(
        "--endpoint",
        action="append",
        required=True,
        metavar="LABEL=KIND[:DETAIL]",
        help=(
            "an endpoint to compare; repeat for each. "
            "e.g. prod=mock:suites/demo-endpoint.json, baseline=mock, "
            "vendor=anthropic:claude-sonnet-4-5"
        ),
    )
    add_journal_args(compare)
    compare.add_argument("--out", default="", help="write a comparison report here")
    compare.add_argument(
        "--format", nargs="+", default=["md"], choices=["md", "html"]
    )
    compare.add_argument("--prepared-by", default="")
    compare.set_defaults(func=cmd_compare)

    probes = subparsers.add_parser("probes", help="list available procedures")
    probes.add_argument("-v", "--verbose", action="store_true")
    probes.set_defaults(func=cmd_probes)

    coverage = subparsers.add_parser(
        "coverage", help="projected framework coverage for a suite"
    )
    coverage.add_argument("suite")
    coverage.add_argument(
        "--all-capabilities",
        action="store_true",
        help="count journal, drift, and workpapers as in use",
    )
    coverage.set_defaults(func=cmd_coverage)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            raise
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
