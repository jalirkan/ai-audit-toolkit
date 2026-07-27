"""Tests for the probe framework, mainly the decision rule.

``decide`` is where measured uncertainty turns into a stated conclusion, so it
gets tested at the boundaries rather than in the middle: a pass that should not
have been granted is the failure mode that would matter most.
"""

from __future__ import annotations

import unittest

from adapters.mock import MockAdapter, MockRule
from core.evidence import (
    OUTCOME_ERROR,
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
    Measurement,
)
from probes.base import (
    HIGHER_IS_BETTER,
    LOWER_IS_BETTER,
    PROBES,
    RULE_INTERVAL,
    RULE_ZERO_TOLERANCE,
    Probe,
    available_probes,
    decide,
    get_probe,
)


class TestZeroToleranceRule(unittest.TestCase):
    """Threshold 0 on a lower-is-better rate = attribute sampling."""

    def test_clean_run_at_or_above_minimum_passes(self):
        d = decide(
            Measurement.proportion("leak_rate", 0, 22),
            threshold=0.0,
            direction=LOWER_IS_BETTER,
            min_sample=20,
        )
        self.assertEqual(d.outcome, OUTCOME_PASS)
        self.assertEqual(d.rule, RULE_ZERO_TOLERANCE)
        self.assertIn("No exceptions noted", d.rationale)

    def test_a_passing_rationale_still_states_the_residual_uncertainty(self):
        d = decide(
            Measurement.proportion("leak_rate", 0, 22),
            threshold=0.0,
            direction=LOWER_IS_BETTER,
            min_sample=20,
        )
        self.assertIn("95% CI", d.rationale)

    def test_clean_run_below_minimum_is_inconclusive_not_pass(self):
        d = decide(
            Measurement.proportion("leak_rate", 0, 5),
            threshold=0.0,
            direction=LOWER_IS_BETTER,
            min_sample=20,
        )
        self.assertEqual(d.outcome, OUTCOME_INCONCLUSIVE)
        self.assertIn("below the minimum", d.rationale)

    def test_a_single_exception_fails(self):
        d = decide(
            Measurement.proportion("leak_rate", 1, 22),
            threshold=0.0,
            direction=LOWER_IS_BETTER,
            min_sample=20,
        )
        self.assertEqual(d.outcome, OUTCOME_FAIL)
        self.assertIn("1 exception(s)", d.rationale)

    def test_exceptions_fail_even_in_a_small_sample(self):
        # Only the reassuring conclusion is gated on sample size.
        d = decide(
            Measurement.proportion("leak_rate", 1, 3),
            threshold=0.0,
            direction=LOWER_IS_BETTER,
            min_sample=20,
        )
        self.assertEqual(d.outcome, OUTCOME_FAIL)


class TestIntervalRuleLowerIsBetter(unittest.TestCase):
    def test_interval_entirely_within_tolerance_passes(self):
        # 0/20 -> 95% CI upper bound about 0.161, below the 0.20 tolerance.
        d = decide(
            Measurement.proportion("rate", 0, 20),
            threshold=0.20,
            direction=LOWER_IS_BETTER,
            min_sample=20,
        )
        self.assertEqual(d.outcome, OUTCOME_PASS)
        self.assertEqual(d.rule, RULE_INTERVAL)

    def test_interval_entirely_above_tolerance_fails(self):
        # 20/30 -> lower bound about 0.49, well above tolerance.
        d = decide(
            Measurement.proportion("rate", 20, 30),
            threshold=0.20,
            direction=LOWER_IS_BETTER,
            min_sample=20,
        )
        self.assertEqual(d.outcome, OUTCOME_FAIL)

    def test_straddling_interval_is_inconclusive(self):
        # 5/20 -> about (0.11, 0.47), which contains the 0.20 tolerance.
        d = decide(
            Measurement.proportion("rate", 5, 20),
            threshold=0.20,
            direction=LOWER_IS_BETTER,
            min_sample=20,
        )
        self.assertEqual(d.outcome, OUTCOME_INCONCLUSIVE)
        self.assertIn("More trials are required", d.rationale)

    def test_within_tolerance_but_undersized_is_inconclusive(self):
        # 0/5 upper bound about 0.43, inside a 0.50 tolerance, but n is small.
        d = decide(
            Measurement.proportion("rate", 0, 5),
            threshold=0.50,
            direction=LOWER_IS_BETTER,
            min_sample=20,
        )
        self.assertEqual(d.outcome, OUTCOME_INCONCLUSIVE)
        self.assertIn("below the minimum", d.rationale)


class TestIntervalRuleHigherIsBetter(unittest.TestCase):
    def test_interval_entirely_above_the_minimum_passes(self):
        # 20/20 -> lower bound about 0.839, above the 0.80 requirement.
        d = decide(
            Measurement.proportion("agreement", 20, 20),
            threshold=0.80,
            direction=HIGHER_IS_BETTER,
            min_sample=20,
        )
        self.assertEqual(d.outcome, OUTCOME_PASS)

    def test_interval_entirely_below_the_minimum_fails(self):
        # 10/30 -> upper bound about 0.51.
        d = decide(
            Measurement.proportion("agreement", 10, 30),
            threshold=0.80,
            direction=HIGHER_IS_BETTER,
            min_sample=20,
        )
        self.assertEqual(d.outcome, OUTCOME_FAIL)

    def test_straddling_interval_is_inconclusive(self):
        # 25/30 -> about (0.66, 0.93), which contains 0.80.
        d = decide(
            Measurement.proportion("agreement", 25, 30),
            threshold=0.80,
            direction=HIGHER_IS_BETTER,
            min_sample=20,
        )
        self.assertEqual(d.outcome, OUTCOME_INCONCLUSIVE)

    def test_meeting_the_minimum_in_an_undersized_sample_is_inconclusive(self):
        d = decide(
            Measurement.proportion("agreement", 4, 4),
            threshold=0.30,
            direction=HIGHER_IS_BETTER,
            min_sample=20,
        )
        self.assertEqual(d.outcome, OUTCOME_INCONCLUSIVE)


class TestDecideEdgeCases(unittest.TestCase):
    def test_no_trials_is_inconclusive(self):
        d = decide(
            Measurement.proportion("rate", 0, 0),
            threshold=0.0,
            direction=LOWER_IS_BETTER,
        )
        self.assertEqual(d.outcome, OUTCOME_INCONCLUSIVE)
        self.assertIn("No trials", d.rationale)

    def test_measurement_without_an_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            decide(
                Measurement.count("exceptions", 1, 10),
                threshold=0.0,
                direction=LOWER_IS_BETTER,
            )

    def test_unknown_direction_is_rejected(self):
        with self.assertRaises(ValueError):
            decide(
                Measurement.proportion("rate", 1, 10),
                threshold=0.1,
                direction="sideways",
            )

    def test_every_rationale_carries_numbers(self):
        for measurement, threshold, direction in (
            (Measurement.proportion("r", 0, 22), 0.0, LOWER_IS_BETTER),
            (Measurement.proportion("r", 1, 22), 0.0, LOWER_IS_BETTER),
            (Measurement.proportion("r", 0, 3), 0.0, LOWER_IS_BETTER),
            (Measurement.proportion("r", 5, 20), 0.2, LOWER_IS_BETTER),
            (Measurement.proportion("r", 20, 20), 0.8, HIGHER_IS_BETTER),
            (Measurement.proportion("r", 10, 30), 0.8, HIGHER_IS_BETTER),
        ):
            with self.subTest(successes=measurement.successes, n=measurement.n):
                d = decide(measurement, threshold=threshold, direction=direction)
                self.assertIn(str(measurement.n), d.rationale)


class _DemoProbe(Probe):
    probe_id = "test-demo-probe"
    title = "Demo"
    procedure = "Sends one prompt."

    def run(self, adapter):
        from core.evidence import Trial, utc_now_iso

        started = utc_now_iso()
        response = adapter.complete("hello")
        measurement = Measurement.proportion("rate", 0, 1)
        return [
            self.build_evidence(
                adapter,
                decision=decide(
                    measurement, threshold=0.0, direction=LOWER_IS_BETTER
                ),
                trials=[Trial(index=0, prompt="hello", response_text=response.text)],
                measurements=[measurement],
                started_at=started,
                unit="only",
            )
        ]


class TestRegistry(unittest.TestCase):
    def test_built_in_probes_are_registered(self):
        import probes  # noqa: F401  (import triggers registration)

        for probe_id in (
            "output-consistency",
            "injection-resistance",
            "citation-faithfulness",
        ):
            with self.subTest(probe_id=probe_id):
                self.assertIn(probe_id, available_probes())

    def test_get_probe_returns_the_class(self):
        self.assertIs(get_probe("test-demo-probe"), _DemoProbe)

    def test_unknown_probe_id_lists_what_is_available(self):
        with self.assertRaises(KeyError) as ctx:
            get_probe("no-such-probe")
        self.assertIn("registered probes", str(ctx.exception))

    def test_duplicate_probe_id_is_refused(self):
        with self.assertRaises(ValueError):

            class _Clashing(Probe):
                probe_id = "test-demo-probe"

                def run(self, adapter):
                    return []

    def test_abstract_probes_without_an_id_are_not_registered(self):
        before = set(PROBES)

        class _Intermediate(Probe):
            def run(self, adapter):
                return []

        self.assertEqual(before, set(PROBES))


class TestRunSafely(unittest.TestCase):
    def test_adapter_failure_becomes_error_evidence_not_a_finding(self):
        adapter = MockAdapter([MockRule(pattern="hello", error="endpoint down")])
        results = _DemoProbe().run_safely(adapter)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, OUTCOME_ERROR)
        self.assertNotEqual(results[0].outcome, OUTCOME_FAIL)
        self.assertIn("endpoint down", results[0].notes)

    def test_successful_run_passes_through(self):
        results = _DemoProbe().run_safely(MockAdapter())
        self.assertEqual(len(results), 1)
        self.assertNotEqual(results[0].outcome, OUTCOME_ERROR)

    def test_error_evidence_is_serializable(self):
        adapter = MockAdapter([MockRule(pattern="hello", error="boom")])
        evidence = _DemoProbe().run_safely(adapter)[0]
        self.assertTrue(evidence.content_hash().startswith("sha256:"))


class TestEvidenceConstruction(unittest.TestCase):
    def test_unit_and_config_are_recorded(self):
        evidence = _DemoProbe().run(MockAdapter())[0]
        self.assertEqual(evidence.config["unit"], "only")
        self.assertEqual(evidence.probe_id, "test-demo-probe")

    def test_notes_carry_the_decision_rationale(self):
        evidence = _DemoProbe().run(MockAdapter())[0]
        self.assertTrue(evidence.notes)
        self.assertIn("sample", evidence.notes.lower())

    def test_fingerprint_matches_the_adapter(self):
        adapter = MockAdapter(seed=7)
        evidence = _DemoProbe().run(adapter)[0]
        self.assertEqual(evidence.fingerprint.digest(), adapter.fingerprint().digest())


if __name__ == "__main__":
    unittest.main()
