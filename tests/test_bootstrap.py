"""Tests for the bootstrap machinery.

Beyond the unit-level checks, two tests validate the procedure statistically,
in the spirit of planted-signal testing: a real difference must be found, and
two samples drawn from the *same* distribution must not be flagged more often
than the confidence level allows. A detector that fires constantly would pass
every "does it detect drift?" test and be worthless.

Both use fixed seeds, so they are deterministic rather than flaky.
"""

from __future__ import annotations

import random
import unittest

from drift.bootstrap import (
    DEFAULT_RESAMPLES,
    DEFAULT_SEED,
    binomial_cdf,
    binomial_pmf,
    bootstrap_proportion_difference,
    newcombe_difference,
)

bpd = bootstrap_proportion_difference


class TestBinomialPmf(unittest.TestCase):
    def test_sums_to_one(self):
        for n, p in ((1, 0.5), (10, 0.3), (40, 0.075), (200, 0.9)):
            with self.subTest(n=n, p=p):
                self.assertAlmostEqual(sum(binomial_pmf(n, p)), 1.0, places=10)

    def test_known_values(self):
        pmf = binomial_pmf(3, 0.5)
        self.assertEqual(len(pmf), 4)
        for value, expected in zip(pmf, (0.125, 0.375, 0.375, 0.125)):
            self.assertAlmostEqual(value, expected, places=12)

    def test_degenerate_probabilities(self):
        self.assertEqual(binomial_pmf(4, 0.0), [1.0, 0.0, 0.0, 0.0, 0.0])
        self.assertEqual(binomial_pmf(4, 1.0), [0.0, 0.0, 0.0, 0.0, 1.0])

    def test_zero_trials(self):
        self.assertEqual(binomial_pmf(0, 0.5), [1.0])

    def test_stays_accurate_at_extreme_p_and_large_n(self):
        # Naive (1-p)**n recurrences underflow here; log-space does not.
        pmf = binomial_pmf(500, 0.995)
        self.assertAlmostEqual(sum(pmf), 1.0, places=8)
        self.assertGreater(pmf[497], 0.0)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            binomial_pmf(-1, 0.5)
        with self.assertRaises(ValueError):
            binomial_pmf(5, 1.5)


class TestBinomialCdf(unittest.TestCase):
    def test_is_monotone_and_ends_at_one(self):
        cdf = binomial_cdf(20, 0.4)
        self.assertEqual(cdf, sorted(cdf))
        self.assertEqual(cdf[-1], 1.0)

    def test_matches_the_pmf(self):
        pmf, cdf = binomial_pmf(10, 0.25), binomial_cdf(10, 0.25)
        self.assertAlmostEqual(cdf[3], sum(pmf[:4]), places=12)


class TestBootstrapDifference(unittest.TestCase):
    def test_point_estimate_is_the_observed_difference(self):
        interval = bpd(5, 20, 10, 20)
        self.assertAlmostEqual(interval.point, 0.25)

    def test_identical_samples_include_zero(self):
        self.assertFalse(bpd(5, 40, 5, 40).excludes_zero)

    def test_large_planted_difference_excludes_zero(self):
        self.assertTrue(bpd(0, 40, 20, 40).excludes_zero)

    def test_one_extra_exception_is_not_significant(self):
        # 0/22 -> 1/22 is one event. Reporting that as drift would make the
        # monitor useless. The outcome change is what catches this case.
        self.assertFalse(bpd(0, 22, 1, 22).excludes_zero)

    def test_two_clean_runs_do_not_prove_the_rates_are_identical(self):
        # Resampling two all-zero samples can only produce zeros, so the raw
        # bootstrap would report [0, 0] here -- an interval claiming certainty
        # that the two rates match, from 22 observations each. The widening to
        # the analytic bound is what stops that.
        interval = bpd(0, 22, 0, 22)
        self.assertTrue(interval.widened)
        self.assertLess(interval.low, 0.0)
        self.assertGreater(interval.high, 0.0)
        self.assertFalse(interval.excludes_zero)

    def test_a_boundary_arm_is_widened_to_the_analytic_bound(self):
        interval = bpd(0, 40, 3, 40)
        analytic_low, analytic_high = newcombe_difference(0, 40, 3, 40)
        self.assertTrue(interval.widened)
        self.assertLessEqual(interval.low, analytic_low + 1e-12)
        self.assertGreaterEqual(interval.high, analytic_high - 1e-12)

    def test_render_says_when_the_analytic_bound_was_used(self):
        self.assertIn("widened", bpd(0, 22, 0, 22).render())

    def test_is_deterministic_for_a_given_seed(self):
        a, b = bpd(3, 30, 9, 30, seed=7), bpd(3, 30, 9, 30, seed=7)
        self.assertEqual((a.low, a.high), (b.low, b.high))

    def test_the_interval_is_stable_across_seeds(self):
        # The seed must not materially change the answer, or the choice of
        # seed would become an input to the conclusion. Bounds often coincide
        # exactly: with n=30 the bootstrap distribution is supported on
        # multiples of 1/30, so neighbouring percentiles land on the same grid
        # point regardless of the draws. That is the discreteness of small
        # samples, not a defect.
        intervals = [bpd(3, 30, 9, 30, seed=s) for s in (1, 2, 3, 17, 20260727)]
        lows = [i.low for i in intervals]
        highs = [i.high for i in intervals]
        self.assertLessEqual(max(lows) - min(lows), 0.05)
        self.assertLessEqual(max(highs) - min(highs), 0.05)

    def test_seeds_really_do_drive_different_draws(self):
        # Guards against a seed that is accepted and then ignored, which would
        # make the determinism test above vacuous. Uses a case away from the
        # boundary, where the bootstrap bounds are the ones reported rather
        # than the analytic widening -- which is seed-independent by nature.
        a = bpd(120, 400, 200, 400, seed=1)
        b = bpd(120, 400, 200, 400, seed=2)
        self.assertFalse(a.widened)
        self.assertNotEqual((a.low, a.high), (b.low, b.high))

    def test_reversing_the_arguments_negates_the_estimate(self):
        forward = bpd(4, 30, 12, 30, seed=5)
        backward = bpd(12, 30, 4, 30, seed=5)
        self.assertAlmostEqual(forward.point, -backward.point)

    def test_higher_confidence_widens_the_interval(self):
        narrow = bpd(5, 40, 10, 40, confidence=0.80)
        wide = bpd(5, 40, 10, 40, confidence=0.99)
        self.assertLess(narrow.high - narrow.low, wide.high - wide.low)

    def test_bigger_samples_narrow_the_interval(self):
        small = bpd(5, 20, 10, 20)
        large = bpd(50, 200, 100, 200)
        self.assertLess(large.high - large.low, small.high - small.low)

    def test_records_its_own_provenance(self):
        interval = bpd(1, 10, 2, 10)
        self.assertEqual(interval.resamples, DEFAULT_RESAMPLES)
        self.assertEqual(interval.seed, DEFAULT_SEED)
        self.assertEqual(interval.confidence, 0.95)

    def test_render_states_seed_and_resamples(self):
        text = bpd(1, 10, 2, 10).render()
        self.assertIn("95% CI", text)
        self.assertIn("seed", text)
        self.assertIn("resamples", text)

    def test_empty_samples_are_refused(self):
        with self.assertRaises(ValueError):
            bpd(0, 0, 1, 10)
        with self.assertRaises(ValueError):
            bpd(1, 10, 0, 0)

    def test_impossible_counts_are_refused(self):
        with self.assertRaises(ValueError):
            bpd(11, 10, 1, 10)

    def test_bad_parameters_are_refused(self):
        with self.assertRaises(ValueError):
            bpd(1, 10, 2, 10, confidence=1.0)
        with self.assertRaises(ValueError):
            bpd(1, 10, 2, 10, resamples=0)


class TestStatisticalBehaviour(unittest.TestCase):
    """Planted-signal discipline: does it find real signal, and only real signal?"""

    def test_false_positive_rate_respects_the_confidence_level(self):
        # Both samples drawn from the same p. A 95% interval should exclude
        # zero in roughly 5% of trials or fewer; the discreteness of small
        # binomials makes the percentile bootstrap conservative here, which is
        # the right direction to err for an audit tool. The bound is generous
        # enough not to be brittle and tight enough to catch a detector that
        # fires on noise.
        for p, n in ((0.10, 40), (0.25, 40), (0.50, 40), (0.05, 100)):
            with self.subTest(p=p, n=n):
                generator = random.Random(99)
                trials, false_positives = 200, 0
                for i in range(trials):
                    a = sum(generator.random() < p for _ in range(n))
                    b = sum(generator.random() < p for _ in range(n))
                    if bpd(a, n, b, n, resamples=2000, seed=1000 + i).excludes_zero:
                        false_positives += 1
                rate = false_positives / trials
                self.assertLessEqual(
                    rate,
                    0.10,
                    f"flagged {rate:.1%} of no-change comparisons at p={p}, n={n}",
                )

    def test_is_never_narrower_than_the_analytic_interval(self):
        # The invariant the widening exists to guarantee. Checked across a grid
        # rather than a handful of cases, because the failure it prevents --
        # claiming drift a boundary sample cannot support -- showed up only at
        # the boundaries and only in about 2% of pairs.
        for n in (10, 20, 22, 40, 60):
            for successes_before in range(0, n + 1, max(1, n // 5)):
                for successes_after in range(0, n + 1, max(1, n // 5)):
                    with self.subTest(before=successes_before, after=successes_after, n=n):
                        ours = bpd(successes_before, n, successes_after, n, resamples=2000)
                        low, high = newcombe_difference(
                            successes_before, n, successes_after, n
                        )
                        self.assertLessEqual(ours.low, low + 1e-12)
                        self.assertGreaterEqual(ours.high, high - 1e-12)

    def test_never_claims_significance_the_analytic_method_denies(self):
        # Before widening, the plain percentile bootstrap did this in 12 of 749
        # pairs, every one of them with an arm at 0 or n -- which is what a
        # clean baseline looks like, and therefore the most common comparison
        # this toolkit performs.
        offenders = []
        for n in (10, 20, 22, 40, 60):
            for successes_before in range(0, n + 1, max(1, n // 5)):
                for successes_after in range(0, n + 1, max(1, n // 5)):
                    ours = bpd(successes_before, n, successes_after, n, resamples=2000)
                    low, high = newcombe_difference(
                        successes_before, n, successes_after, n
                    )
                    if ours.excludes_zero and not (low > 0 or high < 0):
                        offenders.append((successes_before, successes_after, n))
        self.assertEqual(offenders, [], f"anti-conservative cases: {offenders[:5]}")

    def test_a_decisive_difference_survives_the_widening(self):
        # Conservatism is only acceptable if it does not blind the detector.
        self.assertTrue(bpd(0, 40, 20, 40).excludes_zero)
        self.assertTrue(bpd(2, 60, 30, 60).excludes_zero)
        self.assertTrue(bpd(50, 200, 100, 200).excludes_zero)

    def test_a_real_difference_is_detected_most_of_the_time(self):
        # Same harness, but the second sample really does come from a worse
        # distribution. A detector that never fires would pass the test above.
        generator = random.Random(4242)
        trials, detected = 100, 0
        for i in range(trials):
            baseline = sum(generator.random() < 0.05 for _ in range(60))
            current = sum(generator.random() < 0.40 for _ in range(60))
            if bpd(baseline, 60, current, 60, resamples=2000, seed=500 + i).excludes_zero:
                detected += 1
        self.assertGreaterEqual(detected / trials, 0.95)


if __name__ == "__main__":
    unittest.main()
