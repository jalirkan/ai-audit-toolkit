"""Tests for multi-endpoint comparison.

The behaviour most worth pinning down is what the matrix refuses to do: no
overall ranking, and an explicit "not distinguished" list wherever the
intervals overlap. A comparison table that quietly invites the reader to order
endpoints by point estimate would be the easiest way for this tool to mislead.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from adapters.mock import MATCH_ANY, MockAdapter, MockRule, load_mock_script
from battery.runner import BatteryResult
from battery.spec import BatterySpec, ProbeSpec
from compare.matrix import ComparisonMatrix, EndpointRun, run_comparison
from core.evidence import (
    DIRECTION_LOWER_IS_BETTER,
    OUTCOME_FAIL,
    OUTCOME_PASS,
    Evidence,
    Measurement,
    ModelFingerprint,
)
from drift.bootstrap import bootstrap_mean_interval
from journal.store import Journal
from probes.injection import DEFAULT_CANARY
from report.comparison import build_comparison_report
from report.document import render_html, render_markdown

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_FIXTURE = REPO_ROOT / "suites" / "demo-endpoint.json"

PARAPHRASES = [f"Phrasing {i}: how long is standard delivery?" for i in range(20)]

SPEC = BatterySpec(
    name="compare-suite",
    probes=(
        ProbeSpec(probe_id="injection-resistance", config={}),
        ProbeSpec(
            probe_id="output-consistency",
            config={"cases": [{"id": "delivery", "paraphrases": PARAPHRASES}]},
        ),
    ),
)

CLEAN = "I can't share that. Standard delivery takes five to seven business days."


def leaky_adapter() -> MockAdapter:
    return MockAdapter(
        [
            MockRule.make(pattern="base64", responses=f"Sure: {DEFAULT_CANARY}"),
            MockRule.make(responses=CLEAN, mode=MATCH_ANY),
        ],
        model="leaky-v1",
    )


def clean_adapter() -> MockAdapter:
    return MockAdapter.always(CLEAN, model="clean-v1")


def synthetic(label: str, successes: int, n: int, outcome: str) -> EndpointRun:
    evidence = Evidence(
        probe_id="injection-resistance",
        outcome=outcome,
        fingerprint=ModelFingerprint(adapter="mock", model=label),
        started_at="2026-07-27T00:00:00.000000Z",
        finished_at="2026-07-27T00:00:01.000000Z",
        measurements=(
            Measurement.proportion(
                "leak_rate", successes, n, direction=DIRECTION_LOWER_IS_BETTER
            ),
        ),
        config={"unit": "baseline-scenario"},
    )
    return EndpointRun(
        label=label,
        description=f"mock:{label}",
        result=BatteryResult(
            battery="compare-suite",
            run_id=f"run-{label}",
            started_at="2026-07-27T00:00:00.000000Z",
            finished_at="2026-07-27T00:00:01.000000Z",
            fingerprint=ModelFingerprint(adapter="mock", model=label),
            evidence=(evidence,),
        ),
    )


class TestRunComparison(unittest.TestCase):
    def test_runs_the_same_battery_against_each_endpoint(self):
        matrix = run_comparison(
            SPEC, [("clean", clean_adapter()), ("leaky", leaky_adapter())]
        )
        self.assertEqual(matrix.labels, ["clean", "leaky"])
        for endpoint in matrix.endpoints:
            self.assertEqual(endpoint.result.units_tested, 2)

    def test_outcomes_differ_where_the_endpoints_do(self):
        matrix = run_comparison(
            SPEC, [("clean", clean_adapter()), ("leaky", leaky_adapter())]
        )
        unit = ("injection-resistance", "baseline-confidentiality-instruction")
        self.assertEqual(matrix.outcome("clean", unit), OUTCOME_PASS)
        self.assertEqual(matrix.outcome("leaky", unit), OUTCOME_FAIL)

    def test_units_are_listed_once_across_endpoints(self):
        matrix = run_comparison(
            SPEC, [("a", clean_adapter()), ("b", clean_adapter())]
        )
        self.assertEqual(len(matrix.units), 2)

    def test_duplicate_labels_are_refused(self):
        with self.assertRaises(ValueError):
            run_comparison(SPEC, [("a", clean_adapter()), ("a", leaky_adapter())])

    def test_at_least_one_endpoint_is_required(self):
        with self.assertRaises(ValueError):
            run_comparison(SPEC, [])

    def test_every_run_lands_in_one_journal(self):
        journal = Journal()
        self.addCleanup(journal.close)
        run_comparison(
            SPEC, [("clean", clean_adapter()), ("leaky", leaky_adapter())],
            journal=journal,
        )
        self.assertEqual(len(journal.entries_of_kind("run")), 2)
        self.assertTrue(journal.verify().ok)

    def test_unknown_endpoint_reads_as_not_tested(self):
        matrix = run_comparison(SPEC, [("clean", clean_adapter())])
        self.assertEqual(matrix.outcome("nope", matrix.units[0]), "-")


class TestNoRanking(unittest.TestCase):
    def test_the_matrix_exposes_no_score_or_ranking(self):
        matrix = run_comparison(SPEC, [("a", clean_adapter())])
        for forbidden in ("rank", "ranking", "winner", "score", "best"):
            with self.subTest(attribute=forbidden):
                self.assertFalse(hasattr(matrix, forbidden))

    def test_the_summary_states_why_there_is_no_ranking(self):
        matrix = run_comparison(SPEC, [("a", clean_adapter()), ("b", leaky_adapter())])
        text = "\n".join(matrix.summary_lines())
        self.assertIn("No overall ranking", text)
        self.assertIn("not commensurable", text)


class TestOverlapDetection(unittest.TestCase):
    def test_overlapping_intervals_are_reported_as_undistinguished(self):
        # 1/20 versus 2/20: the intervals overlap heavily, so the run has not
        # shown these endpoints to differ, whatever the point estimates say.
        matrix = ComparisonMatrix(
            battery="b",
            endpoints=(
                synthetic("a", 1, 20, OUTCOME_FAIL),
                synthetic("b", 2, 20, OUTCOME_FAIL),
            ),
        )
        names = [r.metric for r in matrix.undistinguished_metrics()]
        self.assertEqual(names, ["leak_rate"])

    def test_separated_intervals_are_not_reported_as_undistinguished(self):
        matrix = ComparisonMatrix(
            battery="b",
            endpoints=(
                synthetic("a", 0, 60, OUTCOME_PASS),
                synthetic("b", 45, 60, OUTCOME_FAIL),
            ),
        )
        self.assertEqual(matrix.undistinguished_metrics(), [])

    def test_a_single_endpoint_is_never_undistinguished(self):
        matrix = ComparisonMatrix(
            battery="b", endpoints=(synthetic("only", 1, 20, OUTCOME_FAIL),)
        )
        self.assertEqual(matrix.undistinguished_metrics(), [])

    def test_summary_names_the_undistinguished_metric(self):
        matrix = ComparisonMatrix(
            battery="b",
            endpoints=(
                synthetic("a", 1, 20, OUTCOME_FAIL),
                synthetic("b", 2, 20, OUTCOME_FAIL),
            ),
        )
        text = "\n".join(matrix.summary_lines())
        self.assertIn("Not distinguished by this run", text)
        self.assertIn("leak_rate", text)


class TestOperationalFigures(unittest.TestCase):
    def test_latency_is_reported_with_an_interval(self):
        matrix = run_comparison(SPEC, [("a", clean_adapter())])
        measurement = matrix.endpoints[0].latency_measurement(resamples=500)
        self.assertIsNotNone(measurement)
        self.assertIsNotNone(measurement.ci_low)
        self.assertIn("95% CI", measurement.render())
        self.assertEqual(measurement.n, matrix.endpoints[0].total_calls)

    def test_call_count_matches_the_run(self):
        matrix = run_comparison(SPEC, [("a", clean_adapter())])
        self.assertEqual(
            matrix.endpoints[0].total_calls, matrix.endpoints[0].result.total_trials
        )


class TestBootstrapMean(unittest.TestCase):
    def test_interval_brackets_the_observed_mean(self):
        values = [10.0, 12.0, 11.0, 30.0, 9.0, 14.0, 13.0, 11.5]
        interval = bootstrap_mean_interval(values, resamples=2000)
        self.assertAlmostEqual(interval.point, sum(values) / len(values))
        self.assertLess(interval.low, interval.point)
        self.assertGreater(interval.high, interval.point)

    def test_is_deterministic_for_a_given_seed(self):
        values = [1.0, 5.0, 3.0, 9.0, 2.0]
        a = bootstrap_mean_interval(values, seed=3, resamples=500)
        b = bootstrap_mean_interval(values, seed=3, resamples=500)
        self.assertEqual((a.low, a.high), (b.low, b.high))

    def test_a_single_observation_yields_an_unbounded_interval(self):
        # Resampling one value would give a zero-width interval implying the
        # mean is known exactly, which one observation cannot establish.
        interval = bootstrap_mean_interval([42.0])
        self.assertEqual(interval.low, float("-inf"))
        self.assertEqual(interval.high, float("inf"))

    def test_more_data_narrows_the_interval(self):
        import random

        rng = random.Random(1)
        small = [rng.gauss(100, 20) for _ in range(10)]
        large = [rng.gauss(100, 20) for _ in range(500)]
        a = bootstrap_mean_interval(small, resamples=1000)
        b = bootstrap_mean_interval(large, resamples=1000)
        self.assertLess(b.high - b.low, a.high - a.low)

    def test_empty_sample_is_refused(self):
        with self.assertRaises(ValueError):
            bootstrap_mean_interval([])


class TestComparisonReport(unittest.TestCase):
    def matrix(self) -> ComparisonMatrix:
        return run_comparison(
            SPEC, [("clean", clean_adapter()), ("leaky", leaky_adapter())]
        )

    def test_markdown_has_every_section(self):
        text = render_markdown(build_comparison_report(self.matrix()))
        for heading in (
            "Endpoints compared",
            "Outcomes by procedure",
            "Measurements",
            "Operational figures",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, text)

    def test_report_refuses_to_rank(self):
        text = render_markdown(build_comparison_report(self.matrix()))
        self.assertIn("No overall ranking is given", text)

    def test_prices_are_explicitly_absent(self):
        text = render_markdown(build_comparison_report(self.matrix()))
        self.assertIn("Prices are deliberately absent", text)

    def test_no_bare_rates_in_either_format(self):
        percent = re.compile(r"\d+(?:\.\d+)?\s*%")
        style = re.compile(r"<style>.*?</style>", re.DOTALL)
        document = build_comparison_report(self.matrix())
        for renderer in (render_markdown, render_html):
            text = style.sub("", renderer(document))
            for line in text.split("\n"):
                if percent.search(line):
                    self.assertIn("CI", line, f"bare rate: {line.strip()[:90]}")

    def test_html_is_standalone(self):
        text = render_html(build_comparison_report(self.matrix()))
        for external in ("http://", "https://", "<script", "<link"):
            self.assertNotIn(external, text)

    def test_matrix_serializes(self):
        from core.canonical import canonical_json

        self.assertTrue(canonical_json(self.matrix().to_dict()))


class TestCliCompare(unittest.TestCase):
    def run_cli(self, *argv):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        import cli

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(argv))
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 2
        return code, out.getvalue() + err.getvalue()

    def test_compares_two_fixture_endpoints(self):
        code, out = self.run_cli(
            "compare",
            str(REPO_ROOT / "suites" / "baseline.json"),
            "--endpoint", f"incumbent=mock:{DEMO_FIXTURE}",
            "--endpoint", "bare=mock",
            "--no-journal",
        )
        self.assertIn(code, (0, 1))
        self.assertIn("incumbent", out)
        self.assertIn("bare", out)
        self.assertIn("No overall ranking", out)

    def test_writes_a_report_when_asked(self):
        with TemporaryDirectory() as tmp:
            self.run_cli(
                "compare",
                str(REPO_ROOT / "suites" / "baseline.json"),
                "--endpoint", f"a=mock:{DEMO_FIXTURE}",
                "--no-journal", "--out", tmp, "--format", "md", "html",
            )
            written = sorted(p.name for p in Path(tmp).glob("*"))
            self.assertEqual(len(written), 2)

    def test_a_malformed_endpoint_is_a_clean_error(self):
        code, out = self.run_cli(
            "compare", str(REPO_ROOT / "suites" / "baseline.json"),
            "--endpoint", "no-equals-sign", "--no-journal",
        )
        self.assertNotEqual(code, 0)
        self.assertIn("label=kind", out)

    def test_an_unknown_endpoint_kind_lists_the_valid_ones(self):
        code, out = self.run_cli(
            "compare", str(REPO_ROOT / "suites" / "baseline.json"),
            "--endpoint", "x=telepathy", "--no-journal",
        )
        self.assertNotEqual(code, 0)
        self.assertIn("mock", out)

    def test_a_missing_fixture_is_a_clean_error(self):
        code, out = self.run_cli(
            "compare", str(REPO_ROOT / "suites" / "baseline.json"),
            "--endpoint", "x=mock:/nonexistent/fixture.json", "--no-journal",
        )
        self.assertNotEqual(code, 0)
        self.assertIn("x", out)


if __name__ == "__main__":
    unittest.main()
