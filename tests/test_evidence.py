"""Tests for core.evidence.

Two themes: serialization must round-trip losslessly (an evidence record that
degrades in storage is not evidence), and the no-bare-rates rule must be
impossible to violate rather than merely discouraged.
"""

from __future__ import annotations

import unittest

from core.evidence import (
    CI_BOOTSTRAP,
    CI_NONE,
    CI_WILSON,
    KIND_COUNT,
    KIND_MEAN,
    KIND_PROPORTION,
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
    SCHEMA_VERSION,
    Evidence,
    Measurement,
    ModelFingerprint,
    Trial,
    normalize_token_usage,
    utc_now_iso,
)


def make_fingerprint(**overrides):
    kwargs = {
        "adapter": "mock",
        "model": "mock-deterministic-v1",
        "params": {"temperature": 0.0, "max_tokens": 512, "seed": 0},
        "system_prompt_hash": None,
    }
    kwargs.update(overrides)
    return ModelFingerprint(**kwargs)


def make_evidence(**overrides):
    kwargs = {
        "probe_id": "injection-resistance",
        "outcome": OUTCOME_PASS,
        "fingerprint": make_fingerprint(),
        "started_at": "2026-07-27T00:00:00.000000Z",
        "finished_at": "2026-07-27T00:00:04.250000Z",
        "trials": (
            Trial(index=0, prompt="p0", response_text="r0", passed=True),
            Trial(
                index=1,
                prompt="p1",
                response_text="r1",
                passed=False,
                labels={"canary": "leaked"},
                latency_ms=42.5,
            ),
        ),
        "measurements": (Measurement.proportion("leak_rate", 1, 2),),
        "config": {"attempts": 2, "canary": "ZZ-TEST"},
        "notes": "scripted run",
    }
    kwargs.update(overrides)
    return Evidence(**kwargs)


class TestMeasurementUncertaintyIsMandatory(unittest.TestCase):
    def test_proportion_without_interval_cannot_be_constructed(self):
        with self.assertRaises(ValueError) as ctx:
            Measurement(name="leak_rate", kind=KIND_PROPORTION, value=0.12, n=25)
        self.assertIn("confidence interval", str(ctx.exception))

    def test_mean_without_interval_cannot_be_constructed(self):
        with self.assertRaises(ValueError):
            Measurement(name="latency", kind=KIND_MEAN, value=120.0, n=25)

    def test_interval_without_a_named_method_is_rejected(self):
        with self.assertRaises(ValueError):
            Measurement(
                name="leak_rate",
                kind=KIND_PROPORTION,
                value=0.12,
                n=25,
                ci_low=0.04,
                ci_high=0.30,
                ci_method=CI_NONE,
                confidence=0.95,
            )

    def test_interval_without_a_confidence_level_is_rejected(self):
        with self.assertRaises(ValueError):
            Measurement(
                name="leak_rate",
                kind=KIND_PROPORTION,
                value=0.12,
                n=25,
                ci_low=0.04,
                ci_high=0.30,
                ci_method=CI_WILSON,
            )

    def test_counts_are_exempt_because_they_are_not_estimates(self):
        m = Measurement.count("exceptions", 3, 25)
        self.assertEqual(m.kind, KIND_COUNT)
        self.assertIsNone(m.ci_low)

    def test_bootstrap_intervals_are_accepted_for_means(self):
        m = Measurement(
            name="latency_ms",
            kind=KIND_MEAN,
            value=118.0,
            n=40,
            ci_low=101.2,
            ci_high=139.9,
            ci_method=CI_BOOTSTRAP,
            confidence=0.95,
        )
        self.assertEqual(m.ci_method, CI_BOOTSTRAP)


class TestMeasurementProportion(unittest.TestCase):
    def test_computes_value_and_wilson_interval(self):
        m = Measurement.proportion("leak_rate", 3, 25)
        self.assertAlmostEqual(m.value, 0.12)
        self.assertEqual(m.n, 25)
        self.assertEqual(m.successes, 3)
        self.assertEqual(m.ci_method, CI_WILSON)
        self.assertAlmostEqual(m.ci_low, 0.04167, places=4)
        self.assertAlmostEqual(m.ci_high, 0.29955, places=4)

    def test_zero_trials_is_marked_uninformative_with_a_full_width_interval(self):
        m = Measurement.proportion("leak_rate", 0, 0)
        self.assertFalse(m.is_informative)
        self.assertEqual((m.ci_low, m.ci_high), (0.0, 1.0))

    def test_perfect_result_still_carries_uncertainty(self):
        # 0 of 8 clean is not proof of a 0% leak rate, and the record must not
        # let a reader think otherwise.
        m = Measurement.proportion("leak_rate", 0, 8)
        self.assertEqual(m.value, 0.0)
        self.assertGreater(m.ci_high, 0.3)

    def test_rejects_successes_above_n(self):
        with self.assertRaises(ValueError):
            Measurement.proportion("leak_rate", 5, 3)

    def test_interval_width(self):
        m = Measurement.proportion("r", 5, 10)
        self.assertAlmostEqual(m.interval_width, m.ci_high - m.ci_low)


class TestMeasurementRendering(unittest.TestCase):
    def test_render_includes_interval_and_counts(self):
        text = Measurement.proportion("leak_rate", 3, 25).render()
        self.assertIn("0.120", text)
        self.assertIn("95% CI", text)
        self.assertIn("3/25", text)

    def test_percent_rendering_still_carries_interval_and_counts(self):
        text = Measurement.proportion("leak_rate", 3, 25).render(as_percent=True)
        self.assertIn("12.0%", text)
        self.assertIn("95% CI", text)
        self.assertIn("3/25", text)

    def test_untested_renders_as_untested_not_as_zero(self):
        text = Measurement.proportion("leak_rate", 0, 0).render()
        self.assertIn("not tested", text)
        self.assertNotIn("0.000 (", text)

    def test_count_renders_as_x_of_n(self):
        self.assertEqual(Measurement.count("exceptions", 3, 25).render(), "3 of 25")

    def test_a_bare_tally_renders_without_a_denominator(self):
        self.assertEqual(Measurement.count("claims_screened", 40, 40).render(), "40")

    def test_non_default_confidence_is_reported_accurately(self):
        text = Measurement.proportion("r", 5, 20, confidence=0.99).render()
        self.assertIn("99% CI", text)


class TestMeasurementSerialization(unittest.TestCase):
    def test_round_trip(self):
        for m in (
            Measurement.proportion("leak_rate", 3, 25),
            Measurement.count("exceptions", 3, 25),
            Measurement.proportion("agreement", 0, 0),
        ):
            with self.subTest(measurement=m.name):
                self.assertEqual(Measurement.from_dict(m.to_dict()), m)


class TestModelFingerprint(unittest.TestCase):
    def test_digest_is_stable(self):
        self.assertEqual(make_fingerprint().digest(), make_fingerprint().digest())

    def test_digest_changes_when_any_parameter_changes(self):
        base = make_fingerprint().digest()
        self.assertNotEqual(base, make_fingerprint(model="other").digest())
        self.assertNotEqual(base, make_fingerprint(adapter="openai").digest())
        self.assertNotEqual(
            base, make_fingerprint(params={"temperature": 0.7}).digest()
        )
        self.assertNotEqual(base, make_fingerprint(system_prompt_hash="sha256:x").digest())

    def test_short_form_is_twelve_hex_characters(self):
        short = make_fingerprint().short()
        self.assertEqual(len(short), 12)
        self.assertTrue(all(c in "0123456789abcdef" for c in short))

    def test_requires_adapter_and_model(self):
        with self.assertRaises(ValueError):
            ModelFingerprint(adapter="", model="m")
        with self.assertRaises(ValueError):
            ModelFingerprint(adapter="mock", model="")

    def test_rejects_unserializable_params(self):
        with self.assertRaises(TypeError):
            ModelFingerprint(adapter="mock", model="m", params={"fn": object()})

    def test_round_trip(self):
        fp = make_fingerprint()
        self.assertEqual(ModelFingerprint.from_dict(fp.to_dict()), fp)


class TestTrial(unittest.TestCase):
    def test_round_trip(self):
        t = Trial(
            index=2,
            prompt="p",
            response_text="r",
            system="s",
            latency_ms=12.5,
            passed=False,
            labels={"canary": "leaked"},
            usage={"prompt_tokens": 12, "completion_tokens": 5},
        )
        self.assertEqual(Trial.from_dict(t.to_dict()), t)

    def test_rejects_negative_index(self):
        with self.assertRaises(ValueError):
            Trial(index=-1, prompt="p", response_text="r")

    def test_rejects_unserializable_labels(self):
        with self.assertRaises(TypeError):
            Trial(index=0, prompt="p", response_text="r", labels={"x": object()})

    def test_usage_none_is_omitted_from_serialization(self):
        # Records written before this field must keep a stable content hash.
        t = Trial(index=0, prompt="p", response_text="r")
        self.assertNotIn("usage", t.to_dict())
        self.assertIsNone(Trial.from_dict(t.to_dict()).usage)

    def test_empty_usage_normalizes_to_unreported(self):
        self.assertIsNone(Trial(index=0, prompt="p", response_text="r", usage={}).usage)
        self.assertIsNone(normalize_token_usage({}))
        self.assertIsNone(normalize_token_usage(None))

    def test_usage_does_not_invent_zeros_for_missing_keys(self):
        usage = normalize_token_usage({"prompt_tokens": 9})
        self.assertEqual(usage, {"prompt_tokens": 9})
        self.assertNotIn("completion_tokens", usage)

    def test_rejects_negative_token_counts(self):
        with self.assertRaises(ValueError):
            Trial(
                index=0,
                prompt="p",
                response_text="r",
                usage={"prompt_tokens": -1},
            )


class TestEvidence(unittest.TestCase):
    def test_round_trip_is_lossless(self):
        original = make_evidence()
        self.assertEqual(Evidence.from_dict(original.to_dict()), original)

    def test_round_trip_preserves_content_hash(self):
        original = make_evidence()
        restored = Evidence.from_dict(original.to_dict())
        self.assertEqual(restored.content_hash(), original.content_hash())

    def test_content_hash_changes_when_any_field_changes(self):
        base = make_evidence().content_hash()
        self.assertNotEqual(base, make_evidence(outcome=OUTCOME_FAIL).content_hash())
        self.assertNotEqual(base, make_evidence(notes="edited").content_hash())
        self.assertNotEqual(
            base, make_evidence(config={"attempts": 3}).content_hash()
        )
        self.assertNotEqual(
            base,
            make_evidence(
                trials=(Trial(index=0, prompt="different", response_text="r0"),)
            ).content_hash(),
        )

    def test_sequences_are_normalized_to_tuples(self):
        e = make_evidence(trials=[Trial(index=0, prompt="p", response_text="r")])
        self.assertIsInstance(e.trials, tuple)
        self.assertIsInstance(e.measurements, tuple)

    def test_records_the_schema_version(self):
        self.assertEqual(make_evidence().to_dict()["schema_version"], SCHEMA_VERSION)

    def test_refuses_to_read_a_future_schema(self):
        payload = make_evidence().to_dict()
        payload["schema_version"] = SCHEMA_VERSION + 1
        with self.assertRaises(ValueError):
            Evidence.from_dict(payload)

    def test_rejects_unknown_outcome(self):
        with self.assertRaises(ValueError):
            make_evidence(outcome="probably-fine")

    def test_rejects_duplicate_measurement_names(self):
        with self.assertRaises(ValueError):
            make_evidence(
                measurements=(
                    Measurement.proportion("leak_rate", 1, 2),
                    Measurement.proportion("leak_rate", 2, 2),
                )
            )

    def test_rejects_unserializable_config(self):
        with self.assertRaises(TypeError):
            make_evidence(config={"callback": object()})

    def test_exceptions_are_the_failing_trials(self):
        e = make_evidence()
        self.assertEqual([t.index for t in e.exceptions], [1])

    def test_sample_size_counts_trials(self):
        self.assertEqual(make_evidence().sample_size, 2)

    def test_primary_measurement_is_the_first(self):
        e = make_evidence(
            measurements=(
                Measurement.proportion("leak_rate", 1, 2),
                Measurement.count("exceptions", 1, 2),
            )
        )
        self.assertEqual(e.primary.name, "leak_rate")
        self.assertEqual(e.measurement("exceptions").value, 1.0)
        self.assertIsNone(e.measurement("absent"))

    def test_primary_is_none_without_measurements(self):
        self.assertIsNone(make_evidence(measurements=()).primary)

    def test_with_outcome_returns_a_modified_copy(self):
        e = make_evidence()
        changed = e.with_outcome(OUTCOME_INCONCLUSIVE, notes="n too small")
        self.assertEqual(e.outcome, OUTCOME_PASS)
        self.assertEqual(changed.outcome, OUTCOME_INCONCLUSIVE)
        self.assertEqual(changed.notes, "n too small")

    def test_summary_carries_uncertainty(self):
        summary = make_evidence().summary()
        self.assertIn("injection-resistance", summary)
        self.assertIn("95% CI", summary)


class TestTimestamps(unittest.TestCase):
    def test_utc_now_is_zulu_suffixed_and_microsecond_precise(self):
        stamp = utc_now_iso()
        self.assertTrue(stamp.endswith("Z"))
        self.assertNotIn("+00:00", stamp)
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


if __name__ == "__main__":
    unittest.main()
