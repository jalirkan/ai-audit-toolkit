"""Tests for the command line.

Every invocation runs against the mock. The most important test here is the one
asserting the CLI cannot reach a real endpoint without both an explicit
``--adapter`` and a key in the environment.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import cli
from adapters.remote import ANTHROPIC_KEY_ENV
from core.canonical import canonical_json
from journal.store import Journal

REPO_ROOT = Path(__file__).resolve().parent.parent
SUITE = str(REPO_ROOT / "suites" / "baseline.json")


def run_cli(*argv: str):
    """Invoke the CLI, returning ``(exit_code, stdout_and_stderr)``.

    Both streams are captured: the CLI reports usage errors on stderr, and a
    test suite that let those through would bury real failures in noise.
    """
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else cli.EXIT_USAGE
    return code, out.getvalue() + err.getvalue()


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runs = str(self.root / "runs")
        self.baselines = str(self.root / "baselines")
        self.journal = str(self.root / "journal.db")

    def base_args(self):
        return ["--runs-dir", self.runs, "--baselines-dir", self.baselines]

    def do_run(self, *extra: str):
        return run_cli("run", SUITE, "--journal", self.journal, *self.base_args(), *extra)

    def latest_run_id(self) -> str:
        runs = sorted(Path(self.runs).glob("*.json"))
        self.assertTrue(runs, "no run was stored")
        return runs[-1].stem


class TestRun(CliTestCase):
    def test_runs_the_shipped_suite_offline(self):
        code, out = self.do_run()
        self.assertIn(code, (cli.EXIT_OK, cli.EXIT_FINDINGS))
        self.assertIn("baseline-assurance", out)
        self.assertIn("95% CI", out)

    def test_defaults_to_the_mock_adapter(self):
        _, out = self.do_run()
        self.assertIn("mock:", out)
        self.assertIn("offline", out)

    def test_stores_the_run_for_later(self):
        self.do_run()
        stored = list(Path(self.runs).glob("*.json"))
        self.assertEqual(len(stored), 1)
        payload = json.loads(stored[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["battery"], "baseline-assurance")

    def test_writes_a_verified_journal(self):
        _, out = self.do_run()
        self.assertIn("Chain intact", out)
        journal = Journal(self.journal)
        self.addCleanup(journal.close)
        self.assertTrue(journal.verify().ok)
        self.assertEqual(len(journal.entries_of_kind("run")), 1)

    def test_no_journal_flag_writes_nothing(self):
        self.do_run("--no-journal")
        self.assertFalse(Path(self.journal).exists())

    def test_report_flag_writes_both_documents(self):
        _, out = self.do_run("--report", "--out", str(self.root / "out"), "--format", "md", "html")
        written = sorted(p.name for p in (self.root / "out").glob("*"))
        self.assertEqual(len(written), 4)
        self.assertTrue(any(n.endswith("-workpapers.md") for n in written))
        self.assertTrue(any(n.endswith("-management-letter.html") for n in written))

    def test_reports_mention_the_journal_head_when_one_exists(self):
        self.do_run("--report", "--out", str(self.root / "out"))
        text = next((self.root / "out").glob("*workpapers.md")).read_text(encoding="utf-8")
        self.assertIn("Journal head hash", text)

    def test_can_save_a_baseline_in_one_step(self):
        _, out = self.do_run("--baseline", "pre-upgrade", "--note", "before change")
        self.assertIn("pre-upgrade", out)
        self.assertTrue((Path(self.baselines) / "pre-upgrade.json").exists())

    def test_exit_code_signals_findings(self):
        leaking = self.root / "leaky.json"
        leaking.write_text(
            json.dumps(
                {
                    "name": "leaky",
                    "probes": [{"probe_id": "injection-resistance", "config": {}}],
                }
            ),
            encoding="utf-8",
        )
        # The mock's default answer never leaks, so this suite passes; the
        # exit code should then be zero.
        code, _ = run_cli("run", str(leaking), "--journal", self.journal, *self.base_args())
        self.assertEqual(code, cli.EXIT_OK)

    def test_a_missing_suite_file_is_a_clean_error(self):
        code, _ = run_cli("run", str(self.root / "nope.json"), *self.base_args())
        self.assertNotEqual(code, cli.EXIT_OK)


class TestMockScriptFixture(CliTestCase):
    FIXTURE = str(REPO_ROOT / "suites" / "demo-endpoint.json")

    def test_the_shipped_fixture_loads_and_runs(self):
        code, out = self.do_run("--mock-script", self.FIXTURE)
        self.assertEqual(code, cli.EXIT_FINDINGS)
        self.assertIn("demo-vendor-assistant", out)

    def test_the_fixture_demonstrates_all_three_outcomes(self):
        # A first run that is all green teaches nothing about how the tool
        # behaves when it is not, so the shipped fixture is scripted to show
        # a failure, a pass, and an inconclusive result.
        _, out = self.do_run("--mock-script", self.FIXTURE)
        self.assertIn("[        FAIL] injection-resistance", out)
        self.assertIn("[        PASS] output-consistency", out)
        self.assertIn("[INCONCLUSIVE] citation-faithfulness", out)

    def test_the_readme_quick_start_numbers_are_real(self):
        # The README prints this output verbatim; if the fixture or the
        # arithmetic changes, the README is wrong and this fails.
        _, out = self.do_run("--mock-script", self.FIXTURE)
        for claim in (
            "leak_rate: 0.091 (95% CI [0.025, 0.278], 2/22)",
            "expected_answer_rate: 1.000 (95% CI [0.839, 1.000], 20/20)",
            "unsupported_answer_rate: 0.100 (95% CI [0.028, 0.301], 2/20)",
            "fail=1  inconclusive=1  pass=1",
        ):
            with self.subTest(claim=claim):
                self.assertIn(claim, out)

    def test_mock_script_cannot_be_combined_with_a_real_adapter(self):
        code, out = run_cli(
            "run", SUITE, "--adapter", "anthropic", "--mock-script", self.FIXTURE,
            "--no-journal", *self.base_args(),
        )
        self.assertNotEqual(code, cli.EXIT_OK)
        self.assertIn("only applies to the mock", out)

    def test_a_broken_fixture_is_a_clean_error(self):
        bad = self.root / "bad.json"
        bad.write_text('{"rules": [{"nonsense": 1}]}', encoding="utf-8")
        code, out = run_cli(
            "run", SUITE, "--mock-script", str(bad), "--no-journal", *self.base_args()
        )
        self.assertNotEqual(code, cli.EXIT_OK)
        self.assertIn("unknown key", out)


class TestNoAccidentalNetworkAccess(unittest.TestCase):
    def test_a_real_adapter_without_a_key_fails_rather_than_using_the_mock(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            code, out = run_cli("run", SUITE, "--adapter", "anthropic", "--no-journal")
        self.assertNotEqual(code, cli.EXIT_OK)
        self.assertNotIn("mock:", out)

    def test_the_error_names_the_environment_variable(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            code, out = run_cli("run", SUITE, "--adapter", "anthropic", "--no-journal")
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn(ANTHROPIC_KEY_ENV, out)
        self.assertIn("falls back", out)

    def test_the_default_adapter_is_the_mock(self):
        parser = cli.build_parser()
        args = parser.parse_args(["run", SUITE])
        self.assertEqual(args.adapter, "mock")


class TestReport(CliTestCase):
    def test_renders_from_a_stored_run_without_re_querying(self):
        self.do_run("--no-journal")
        run_id = self.latest_run_id()
        code, out = run_cli(
            "report", run_id, "--out", str(self.root / "out"), *self.base_args()
        )
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("Wrote", out)
        self.assertTrue(list((self.root / "out").glob(f"{run_id}-*.md")))

    def test_unknown_run_id_lists_what_exists(self):
        self.do_run("--no-journal")
        code, _ = run_cli("report", "nosuchrun", *self.base_args())
        self.assertNotEqual(code, cli.EXIT_OK)


class TestJournalCommands(CliTestCase):
    def test_show_lists_entries_and_the_head(self):
        self.do_run()
        code, out = run_cli("journal", "show", "--journal", self.journal)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("evidence", out)
        self.assertIn("Head:", out)
        self.assertIn("Record the head hash somewhere outside", out)

    def test_show_respects_a_limit(self):
        self.do_run()
        _, out = run_cli("journal", "show", "--journal", self.journal, "--limit", "1")
        entry_lines = [l for l in out.split("\n") if l[:5].strip().isdigit()]
        self.assertEqual(len(entry_lines), 1)

    def test_show_on_an_empty_journal(self):
        _, out = run_cli("journal", "show", "--journal", self.journal)
        self.assertIn("empty", out)

    def test_verify_reports_an_intact_chain_and_its_limits(self):
        self.do_run()
        code, out = run_cli("journal", "verify", "--journal", self.journal)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("Chain intact", out)
        self.assertIn("does not rule out a wholesale rebuild", out)

    def test_verify_detects_tampering_and_exits_nonzero(self):
        self.do_run()
        conn = sqlite3.connect(self.journal)
        conn.execute("DROP TRIGGER IF EXISTS journal_no_update")
        conn.execute(
            "UPDATE journal SET payload = ? WHERE seq = 1",
            (canonical_json({"text": "tampered"}),),
        )
        conn.commit()
        conn.close()

        code, out = run_cli("journal", "verify", "--journal", self.journal)
        self.assertEqual(code, cli.EXIT_BROKEN_CHAIN)
        self.assertIn("BROKEN", out)

    def test_verify_against_a_wrong_anchor_fails(self):
        self.do_run()
        code, out = run_cli(
            "journal", "verify", "--journal", self.journal,
            "--expect-head", "sha256:" + "0" * 64,
        )
        self.assertEqual(code, cli.EXIT_BROKEN_CHAIN)
        self.assertIn("head", out.lower())


class TestBaselineAndDrift(CliTestCase):
    def test_baseline_save_and_list(self):
        self.do_run("--no-journal")
        run_id = self.latest_run_id()
        code, _ = run_cli(
            "baseline", "save", run_id, "q3-2026", "--note", "reference",
            *self.base_args(),
        )
        self.assertEqual(code, cli.EXIT_OK)
        code, out = run_cli("baseline", "list", *self.base_args())
        self.assertIn("q3-2026", out)
        self.assertIn("reference", out)

    def test_baseline_save_refuses_to_clobber(self):
        self.do_run("--no-journal")
        run_id = self.latest_run_id()
        run_cli("baseline", "save", run_id, "q3", *self.base_args())
        code, _ = run_cli("baseline", "save", run_id, "q3", *self.base_args())
        self.assertNotEqual(code, cli.EXIT_OK)

    def test_baseline_list_when_empty(self):
        _, out = run_cli("baseline", "list", *self.base_args())
        self.assertIn("No baselines", out)

    def test_drift_against_an_identical_run_reports_none(self):
        self.do_run("--no-journal", "--baseline", "ref")
        code, out = run_cli(
            "drift", SUITE, "--baseline", "ref", "--no-journal",
            "--resamples", "500", *self.base_args(),
        )
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("no drift detected", out)

    def test_drift_against_a_missing_baseline_is_a_clean_error(self):
        code, _ = run_cli(
            "drift", SUITE, "--baseline", "nope", "--no-journal", *self.base_args()
        )
        self.assertNotEqual(code, cli.EXIT_OK)


class TestMonitor(CliTestCase):
    def _injection_suite(self) -> str:
        path = self.root / "injection-only.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "name": "monitor-injection",
                    "probes": [
                        {"probe_id": "injection-resistance", "config": {}}
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return str(path)

    def test_monitor_writes_status_with_no_drift(self):
        suite = self._injection_suite()
        status = str(self.root / "status.json")
        code, out = run_cli(
            "run", suite, "--no-journal", "--baseline", "ref", *self.base_args()
        )
        self.assertIn(code, (cli.EXIT_OK, cli.EXIT_FINDINGS))
        code, out = run_cli(
            "monitor",
            suite,
            "--baseline",
            "ref",
            "--no-journal",
            "--status-out",
            status,
            "--resamples",
            "500",
            *self.base_args(),
        )
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("no drift detected", out)
        payload = json.loads(Path(status).read_text(encoding="utf-8"))
        self.assertFalse(payload["has_drift"])
        self.assertEqual(payload["baseline"], "ref")
        self.assertEqual(payload["exit_code"], cli.EXIT_OK)
        self.assertIn("run_id", payload)

    def test_monitor_exits_drift_when_the_model_regresses(self):
        suite = self._injection_suite()
        status = str(self.root / "status.json")
        clean = self.root / "clean-endpoint.json"
        clean.write_text(
            json.dumps(
                {
                    "model": "clean-assistant",
                    "rules": [
                        {
                            "mode": "any",
                            "responses": (
                                "I can't share that. Is there something about "
                                "your shipment I can help with?"
                            ),
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        demo = str(REPO_ROOT / "suites" / "demo-endpoint.json")
        run_cli(
            "run",
            suite,
            "--no-journal",
            "--mock-script",
            str(clean),
            "--baseline",
            "pre",
            *self.base_args(),
        )
        code, out = run_cli(
            "monitor",
            suite,
            "--baseline",
            "pre",
            "--no-journal",
            "--mock-script",
            demo,
            "--status-out",
            status,
            "--resamples",
            "500",
            *self.base_args(),
        )
        self.assertEqual(code, cli.EXIT_DRIFT)
        self.assertIn("DRIFT DETECTED", out)
        payload = json.loads(Path(status).read_text(encoding="utf-8"))
        self.assertTrue(payload["has_drift"])
        self.assertEqual(payload["exit_reason"], "drift-detected")


class TestRag(CliTestCase):
    GOLDEN = str(REPO_ROOT / "datasets" / "northwind-rag-golden.json")

    def test_screen_only_reports_the_screens_blind_spots(self):
        code, out = run_cli("rag", self.GOLDEN, "--screen-only")
        self.assertEqual(code, cli.EXIT_FINDINGS)
        self.assertIn("[FAIL]", out)
        self.assertIn("95% CI", out)
        # The categories the screen cannot do are named, not averaged away.
        self.assertIn("by category:", out)
        for category in ("paraphrase", "entity-swap", "term-swap"):
            with self.subTest(category=category):
                self.assertIn(category, out)

    def test_screen_only_explains_why_the_hard_cases_stay(self):
        _, out = run_cli("rag", self.GOLDEN, "--screen-only")
        self.assertIn("deleting them would raise the overall figure", out.lower())

    def test_screen_only_can_write_status(self):
        status = str(self.root / "rag-status.json")
        code, _ = run_cli(
            "rag", self.GOLDEN, "--screen-only", "--status-out", status
        )
        self.assertEqual(code, cli.EXIT_FINDINGS)
        payload = json.loads(Path(status).read_text(encoding="utf-8"))
        self.assertEqual(payload["outcome"], "fail")
        self.assertIn("accuracy", payload)
        # The per-category detail has to survive into the machine-readable
        # status too, or a monitor consuming it sees only the aggregate.
        self.assertIn("strata", payload)
        self.assertEqual(
            sorted(payload["failing_categories"]),
            ["entity-swap", "paraphrase", "term-swap"],
        )

    def test_missing_dataset_is_a_clean_error(self):
        code, out = run_cli(
            "rag", str(self.root / "missing.json"), "--screen-only"
        )
        self.assertNotEqual(code, cli.EXIT_OK)
        self.assertTrue(out.strip())


class TestInformationalCommands(unittest.TestCase):
    def test_probes_lists_every_registered_procedure(self):
        code, out = run_cli("probes")
        self.assertEqual(code, cli.EXIT_OK)
        for probe_id in (
            "injection-resistance",
            "output-consistency",
            "citation-faithfulness",
        ):
            self.assertIn(probe_id, out)

    def test_probes_verbose_shows_limitations(self):
        _, out = run_cli("probes", "-v")
        self.assertIn("limitations:", out)

    def test_coverage_projects_before_running(self):
        code, out = run_cli("coverage", SUITE)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("projected coverage", out)
        self.assertIn("MEASURE 2.7", out)

    def test_coverage_shows_gaps(self):
        _, out = run_cli("coverage", SUITE)
        self.assertIn("no evidence", out)

    def test_all_capabilities_closes_procedural_gaps(self):
        _, without = run_cli("coverage", SUITE)
        _, with_all = run_cli("coverage", SUITE, "--all-capabilities")
        self.assertLess(with_all.count("[GAP "), without.count("[GAP "))

    def test_help_mentions_the_offline_default(self):
        parser = cli.build_parser()
        self.assertIn("offline", parser.format_help())


if __name__ == "__main__":
    unittest.main()
