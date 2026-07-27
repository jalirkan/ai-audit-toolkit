"""Tests for core.stats."""

from __future__ import annotations

import unittest

from core.stats import (
    DEFAULT_CONFIDENCE,
    clamp_unit,
    mean,
    wilson_interval,
    z_for_confidence,
)


class TestZForConfidence(unittest.TestCase):
    def test_95_percent_is_the_familiar_constant(self):
        self.assertAlmostEqual(z_for_confidence(0.95), 1.959963985, places=6)

    def test_99_percent(self):
        self.assertAlmostEqual(z_for_confidence(0.99), 2.575829304, places=6)

    def test_higher_confidence_gives_wider_z(self):
        self.assertGreater(z_for_confidence(0.99), z_for_confidence(0.90))

    def test_rejects_out_of_range(self):
        for bad in (0.0, 1.0, -0.5, 1.5):
            with self.subTest(confidence=bad):
                with self.assertRaises(ValueError):
                    z_for_confidence(bad)


class TestWilsonInterval(unittest.TestCase):
    """Reference values cross-checked against published Wilson tables."""

    def test_zero_successes_has_nonzero_upper_bound(self):
        # The normal approximation would report [0, 0] here and imply
        # certainty from 10 observations. Wilson does not.
        low, high = wilson_interval(0, 10)
        self.assertEqual(low, 0.0)
        self.assertAlmostEqual(high, 0.27753, places=4)

    def test_all_successes_has_sub_one_lower_bound(self):
        low, high = wilson_interval(10, 10)
        self.assertAlmostEqual(low, 0.72247, places=4)
        self.assertEqual(high, 1.0)

    def test_half_and_half_is_symmetric(self):
        low, high = wilson_interval(5, 10)
        self.assertAlmostEqual(low, 0.23659, places=4)
        self.assertAlmostEqual(high, 0.76341, places=4)
        self.assertAlmostEqual((low + high) / 2, 0.5, places=9)

    def test_small_rate_reference_value(self):
        low, high = wilson_interval(3, 25)
        self.assertAlmostEqual(low, 0.04167, places=4)
        self.assertAlmostEqual(high, 0.29955, places=4)

    def test_no_sample_means_total_ignorance(self):
        self.assertEqual(wilson_interval(0, 0), (0.0, 1.0))

    def test_interval_always_brackets_the_point_estimate(self):
        for n in (1, 5, 20, 100, 997):
            for successes in {0, 1, n // 3, n // 2, n - 1, n}:
                if not 0 <= successes <= n:
                    continue
                with self.subTest(successes=successes, n=n):
                    low, high = wilson_interval(successes, n)
                    self.assertLessEqual(low, successes / n)
                    self.assertLessEqual(successes / n, high)

    def test_interval_stays_inside_unit_range(self):
        for n in (1, 3, 50, 1000):
            for successes in (0, 1, n):
                with self.subTest(successes=successes, n=n):
                    low, high = wilson_interval(successes, n)
                    self.assertGreaterEqual(low, 0.0)
                    self.assertLessEqual(high, 1.0)

    def test_interval_narrows_as_sample_grows(self):
        widths = []
        for n in (10, 100, 1000, 10000):
            low, high = wilson_interval(n // 2, n)
            widths.append(high - low)
        self.assertEqual(widths, sorted(widths, reverse=True))

    def test_higher_confidence_widens_interval(self):
        narrow = wilson_interval(5, 20, 0.80)
        wide = wilson_interval(5, 20, 0.99)
        self.assertLess(narrow[1] - narrow[0], wide[1] - wide[0])

    def test_default_confidence_is_95_percent(self):
        self.assertEqual(wilson_interval(5, 20), wilson_interval(5, 20, DEFAULT_CONFIDENCE))
        self.assertEqual(DEFAULT_CONFIDENCE, 0.95)

    def test_rejects_impossible_inputs(self):
        with self.assertRaises(ValueError):
            wilson_interval(5, 3)
        with self.assertRaises(ValueError):
            wilson_interval(-1, 10)
        with self.assertRaises(ValueError):
            wilson_interval(1, -10)


class TestHelpers(unittest.TestCase):
    def test_clamp_unit(self):
        self.assertEqual(clamp_unit(-0.2), 0.0)
        self.assertEqual(clamp_unit(1.2), 1.0)
        self.assertEqual(clamp_unit(0.4), 0.4)

    def test_mean_of_empty_is_zero_not_an_exception(self):
        self.assertEqual(mean([]), 0.0)

    def test_mean(self):
        self.assertAlmostEqual(mean([1.0, 2.0, 6.0]), 3.0)


if __name__ == "__main__":
    unittest.main()
