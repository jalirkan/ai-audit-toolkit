"""Tests for baselines and drift comparison."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from adapters.mock import MATCH_ANY, MockAdapter, MockRule
from battery.runner import BatteryResult, run_battery
from battery.spec import BatterySpec, ProbeSpec
from core.evidence import (
    DIRECTION_HIGHER_IS_BETTER,
    DIRECTION_LOWER_IS_BETTER,
    OUTCOME_FAIL,
    OUTCOME_PASS,
    Evidence,
    Measurement,
    ModelFingerprint,
)
from drift.baseline import Baseline, BaselineStore, validate_label
from drift.compare import (
    VERDICT_IMPROVEMENT,
    VERDICT_NO_CHANGE,
    VERDICT_NOT_COMPARABLE,
    VERDICT_REGRESSION,
    compare_measurements,
    compare_runs,
)
from probes.injection import DEFAULT_CANARY

REFUSAL = "I can't share that value."


def fingerprint(**overrides) -> ModelFingerprint:
    kwargs = {
        "adapter": "mock",
        "model": "mock-deterministic-v1",
        "params": {"temperature": 0.0, "max_tokens": 512, "seed": 0},
    }
    kwargs.update(overrides)
    return ModelFingerprint(**kwargs)


def evidence(
    probe_id: str = "injection-resistance",
    unit: str = "baseline-scenario",
    *,
    successes: int = 0,
    n: int = 40,
    metric: str = "leak_rate",
    direction: str = DIRECTION_LOWER_IS_BETTER,
    outcome: str = OUTCOME_PASS,
    config_extra: dict = None,
) -> Evidence:
    config = {"unit": unit, "min_sample": 20}
    if config_extra:
        config.update(config_extra)
    return Evidence(
        probe_id=probe_id,
        outcome=outcome,
        fingerprint=fingerprint(),
        started_at="2026-07-27T00:00:00.000000Z",
        finished_at="2026-07-27T00:00:10.000000Z",
        measurements=(
            Measurement.proportion(metric, successes, n, direction=direction),
        ),
        config=config,
    )


def result(*items: Evidence, run_id: str = "run-a", fp: ModelFingerprint = None) -> BatteryResult:
    return BatteryResult(
        battery="test-suite",
        run_id=run_id,
        started_at="2026-07-27T00:00:00.000000Z",
        finished_at="2026-07-27T00:01:00.000000Z",
        fingerprint=fp or fingerprint(),
        evidence=items,
    )


class TestLabelValidation(unittest.TestCase):
    def test_accepts_reasonable_labels(self):
        for label in ("q3-2026", "pre_upgrade", "v2.1", "a"):
            with self.subTest(label=label):
                self.assertEqual(validate_label(label), label)

    def test_rejects_path_separators_and_traversal(self):
        for label in ("../escape", "dir/name", "a\\b", ".hidden", ""):
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    validate_label(label)

    def test_rejects_overlong_labels(self):
        with self.assertRaises(ValueError):
            validate_label("x" * 65)


class TestBaselineStore(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = BaselineStore(Path(self.tmp.name) / "baselines")
        self.result = result(evidence())

    def test_save_then_load_round_trips(self):
        self.store.save("q3", self.result, note="before the upgrade")
        loaded = self.store.load("q3")
        self.assertEqual(loaded.label, "q3")
        self.assertEqual(loaded.note, "before the upgrade")
        self.assertEqual(loaded.result.evidence, self.result.evidence)

    def test_labels_are_listed(self):
        self.store.save("alpha", self.result)
        self.store.save("beta", self.result)
        self.assertEqual(self.store.labels(), ["alpha", "beta"])

    def test_labels_is_empty_before_anything_is_saved(self):
        self.assertEqual(self.store.labels(), [])

    def test_saving_over_an_existing_baseline_is_refused(self):
        self.store.save("q3", self.result)
        with self.assertRaises(FileExistsError):
            self.store.save("q3", self.result)

    def test_overwrite_must_be_explicit(self):
        self.store.save("q3", self.result)
        replacement = result(evidence(successes=5), run_id="run-b")
        self.store.save("q3", replacement, overwrite=True)
        self.assertEqual(self.store.load("q3").result.run_id, "run-b")

    def test_loading_an_unknown_label_lists_what_exists(self):
        self.store.save("alpha", self.result)
        with self.assertRaises(FileNotFoundError) as ctx:
            self.store.load("missing")
        self.assertIn("alpha", str(ctx.exception))

    def test_future_schema_is_refused(self):
        payload = Baseline(
            label="x", saved_at="t", result=self.result
        ).to_dict()
        payload["schema_version"] = 99
        with self.assertRaises(ValueError):
            Baseline.from_dict(payload)

    def test_exists(self):
        self.assertFalse(self.store.exists("q3"))
        self.store.save("q3", self.result)
        self.assertTrue(self.store.exists("q3"))


class TestMetricComparison(unittest.TestCase):
    def test_planted_regression_is_detected(self):
        comparison = compare_measurements(
            "injection-resistance",
            "u",
            Measurement.proportion("leak_rate", 0, 40, direction=DIRECTION_LOWER_IS_BETTER),
            Measurement.proportion("leak_rate", 20, 40, direction=DIRECTION_LOWER_IS_BETTER),
        )
        self.assertEqual(comparison.verdict, VERDICT_REGRESSION)
        self.assertTrue(comparison.is_significant)
        self.assertAlmostEqual(comparison.delta, 0.5)

    def test_noise_is_not_reported_as_drift(self):
        comparison = compare_measurements(
            "injection-resistance",
            "u",
            Measurement.proportion("leak_rate", 5, 40, direction=DIRECTION_LOWER_IS_BETTER),
            Measurement.proportion("leak_rate", 6, 40, direction=DIRECTION_LOWER_IS_BETTER),
        )
        self.assertEqual(comparison.verdict, VERDICT_NO_CHANGE)
        self.assertFalse(comparison.is_significant)

    def test_direction_decides_whether_a_rise_is_bad(self):
        rising_leaks = compare_measurements(
            "p", "u",
            Measurement.proportion("leak_rate", 2, 60, direction=DIRECTION_LOWER_IS_BETTER),
            Measurement.proportion("leak_rate", 30, 60, direction=DIRECTION_LOWER_IS_BETTER),
        )
        rising_agreement = compare_measurements(
            "p", "u",
            Measurement.proportion("consensus_rate", 2, 60, direction=DIRECTION_HIGHER_IS_BETTER),
            Measurement.proportion("consensus_rate", 30, 60, direction=DIRECTION_HIGHER_IS_BETTER),
        )
        self.assertEqual(rising_leaks.verdict, VERDICT_REGRESSION)
        self.assertEqual(rising_agreement.verdict, VERDICT_IMPROVEMENT)

    def test_falling_agreement_is_a_regression(self):
        comparison = compare_measurements(
            "p", "u",
            Measurement.proportion("consensus_rate", 58, 60, direction=DIRECTION_HIGHER_IS_BETTER),
            Measurement.proportion("consensus_rate", 20, 60, direction=DIRECTION_HIGHER_IS_BETTER),
        )
        self.assertEqual(comparison.verdict, VERDICT_REGRESSION)

    def test_counts_are_not_compared_statistically(self):
        comparison = compare_measurements(
            "p", "u",
            Measurement.count("clusters", 1, 20),
            Measurement.count("clusters", 6, 20),
        )
        self.assertEqual(comparison.verdict, VERDICT_NOT_COMPARABLE)
        self.assertIsNone(comparison.interval)

    def test_an_untested_side_is_not_comparable(self):
        comparison = compare_measurements(
            "p", "u",
            Measurement.proportion("rate", 0, 0, direction=DIRECTION_LOWER_IS_BETTER),
            Measurement.proportion("rate", 3, 20, direction=DIRECTION_LOWER_IS_BETTER),
        )
        self.assertEqual(comparison.verdict, VERDICT_NOT_COMPARABLE)
        self.assertIn("no trials", comparison.detail)

    def test_render_shows_both_sides_with_uncertainty(self):
        comparison = compare_measurements(
            "p", "u",
            Measurement.proportion("leak_rate", 0, 40, direction=DIRECTION_LOWER_IS_BETTER),
            Measurement.proportion("leak_rate", 20, 40, direction=DIRECTION_LOWER_IS_BETTER),
        )
        text = comparison.render()
        self.assertIn("95% CI", text)
        self.assertIn("->", text)
        self.assertIn(VERDICT_REGRESSION, text)


class TestCompareRuns(unittest.TestCase):
    def test_no_drift_between_identical_runs(self):
        report = compare_runs(result(evidence()), result(evidence(), run_id="run-b"))
        self.assertFalse(report.has_drift)
        self.assertTrue(report.comparable)
        self.assertEqual(report.regressions, ())

    def test_planted_drift_is_detected(self):
        before = result(evidence(successes=0, n=40))
        after = result(
            evidence(successes=20, n=40, outcome=OUTCOME_FAIL), run_id="run-b"
        )
        report = compare_runs(before, after, baseline_label="q3")
        self.assertTrue(report.has_drift)
        self.assertEqual(len(report.regressions), 1)
        self.assertEqual(report.regressions[0].metric, "leak_rate")

    def test_noise_alone_does_not_report_drift(self):
        before = result(evidence(successes=5, n=40))
        after = result(evidence(successes=6, n=40), run_id="run-b")
        report = compare_runs(before, after)
        self.assertFalse(report.has_drift)

    def test_a_worsened_outcome_counts_as_drift_without_a_significant_rate_change(self):
        # 0/22 -> 1/22 is not statistically significant, but under a
        # zero-tolerance control it flips pass to fail, and that matters.
        before = result(evidence(successes=0, n=22, outcome=OUTCOME_PASS))
        after = result(
            evidence(successes=1, n=22, outcome=OUTCOME_FAIL), run_id="run-b"
        )
        report = compare_runs(before, after)
        self.assertEqual(report.regressions, ())
        self.assertTrue(report.has_drift)
        self.assertEqual(len(report.worsened_units), 1)

    def test_an_improved_outcome_is_not_drift(self):
        before = result(evidence(successes=10, n=40, outcome=OUTCOME_FAIL))
        after = result(
            evidence(successes=0, n=40, outcome=OUTCOME_PASS), run_id="run-b"
        )
        report = compare_runs(before, after)
        self.assertFalse(report.has_drift)
        self.assertEqual(len(report.improvements), 1)

    def test_added_and_removed_units_are_reported(self):
        before = result(evidence(unit="alpha"), evidence(unit="beta"))
        after = result(
            evidence(unit="alpha"), evidence(unit="gamma"), run_id="run-b"
        )
        report = compare_runs(before, after)
        self.assertEqual(report.added_units, (("injection-resistance", "gamma"),))
        self.assertEqual(report.removed_units, (("injection-resistance", "beta"),))
        self.assertFalse(report.comparable)
        self.assertEqual(len(report.units), 1)

    def test_a_changed_probe_configuration_is_flagged(self):
        before = result(evidence(config_extra={"canary": "AAA"}))
        after = result(evidence(config_extra={"canary": "BBB"}), run_id="run-b")
        report = compare_runs(before, after)
        self.assertTrue(report.units[0].config_changed)
        self.assertFalse(report.comparable)

    def test_an_unchanged_configuration_is_not_flagged(self):
        before = result(evidence(config_extra={"canary": "AAA"}))
        after = result(evidence(config_extra={"canary": "AAA"}), run_id="run-b")
        self.assertTrue(compare_runs(before, after).comparable)

    def test_a_changed_model_is_reported_field_by_field(self):
        before = result(evidence())
        after = result(
            evidence(),
            run_id="run-b",
            fp=fingerprint(model="mock-v2", params={"temperature": 0.7, "max_tokens": 512, "seed": 0}),
        )
        report = compare_runs(before, after)
        self.assertTrue(report.fingerprint_changed)
        differences = report.fingerprint_differences
        self.assertEqual(differences["model"], ("mock-deterministic-v1", "mock-v2"))
        self.assertEqual(differences["params.temperature"], (0.0, 0.7))

    def test_an_unchanged_model_reports_no_differences(self):
        report = compare_runs(result(evidence()), result(evidence(), run_id="run-b"))
        self.assertFalse(report.fingerprint_changed)
        self.assertEqual(report.fingerprint_differences, {})

    def test_metrics_missing_from_the_baseline_are_skipped(self):
        before = result(evidence(metric="leak_rate"))
        after = result(evidence(metric="a_new_metric"), run_id="run-b")
        report = compare_runs(before, after)
        self.assertEqual(report.units[0].metrics, ())


class TestDriftReportRendering(unittest.TestCase):
    def test_clean_summary_says_no_drift(self):
        report = compare_runs(result(evidence()), result(evidence(), run_id="run-b"))
        text = "\n".join(report.summary_lines())
        self.assertIn("no drift detected", text)
        self.assertIn("same model configuration", text)

    def test_drift_summary_leads_with_the_verdict(self):
        before = result(evidence(successes=0, n=40))
        after = result(evidence(successes=20, n=40, outcome=OUTCOME_FAIL), run_id="run-b")
        text = "\n".join(compare_runs(before, after, baseline_label="q3").summary_lines())
        self.assertIn("DRIFT DETECTED", text)
        self.assertIn("q3", text)
        self.assertIn("leak_rate", text)

    def test_summary_warns_when_the_runs_are_not_like_for_like(self):
        before = result(evidence(unit="alpha"))
        after = result(evidence(unit="beta"), run_id="run-b")
        text = "\n".join(compare_runs(before, after).summary_lines())
        self.assertIn("not like-for-like", text)

    def test_summary_reports_a_changed_model(self):
        before = result(evidence())
        after = result(evidence(), run_id="run-b", fp=fingerprint(model="mock-v2"))
        text = "\n".join(compare_runs(before, after).summary_lines())
        self.assertIn("model configuration changed", text)
        self.assertIn("mock-v2", text)

    def test_report_serializes(self):
        from core.canonical import canonical_json

        before = result(evidence(successes=0, n=40))
        after = result(evidence(successes=20, n=40), run_id="run-b")
        payload = compare_runs(before, after).to_dict()
        self.assertTrue(canonical_json(payload))
        self.assertTrue(payload["has_drift"])


class TestEndToEndDrift(unittest.TestCase):
    """A real battery, baselined, re-run against a worse model."""

    SPEC = BatterySpec(
        name="drift-suite",
        probes=(ProbeSpec(probe_id="injection-resistance", config={}),),
    )

    def test_a_model_that_starts_leaking_is_caught(self):
        with TemporaryDirectory() as tmp:
            store = BaselineStore(Path(tmp) / "baselines")

            clean = run_battery(self.SPEC, MockAdapter.always(REFUSAL))
            store.save("pre-upgrade", clean, note="before the model change")

            leaking = MockAdapter(
                [
                    MockRule.make(pattern="base64", responses=f"Sure: {DEFAULT_CANARY}"),
                    MockRule.make(pattern="maintenance", responses=DEFAULT_CANARY),
                    MockRule.make(pattern="developer", responses=DEFAULT_CANARY),
                    MockRule.make(pattern="debugging", responses=DEFAULT_CANARY),
                    MockRule.make(pattern="poem", responses=DEFAULT_CANARY),
                    MockRule.make(pattern="backwards", responses=DEFAULT_CANARY),
                    MockRule.make(pattern="audit log", responses=DEFAULT_CANARY),
                    MockRule.make(responses=REFUSAL, mode=MATCH_ANY),
                ]
            )
            after = run_battery(self.SPEC, leaking)

            report = compare_runs(
                store.load("pre-upgrade").result, after, baseline_label="pre-upgrade"
            )

            self.assertTrue(report.has_drift)
            self.assertTrue(report.comparable)
            self.assertEqual(len(report.regressions), 1)
            self.assertEqual(report.regressions[0].metric, "leak_rate")
            self.assertTrue(report.worsened_units)

    def test_re_running_the_same_model_reports_no_drift(self):
        first = run_battery(self.SPEC, MockAdapter.always(REFUSAL))
        second = run_battery(self.SPEC, MockAdapter.always(REFUSAL))
        report = compare_runs(first, second)
        self.assertFalse(report.has_drift)
        self.assertTrue(report.comparable)
        self.assertFalse(report.fingerprint_changed)


if __name__ == "__main__":
    unittest.main()
