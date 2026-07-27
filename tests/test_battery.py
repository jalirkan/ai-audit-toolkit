"""Tests for battery specs and the battery runner."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from adapters.mock import MATCH_ANY, MockAdapter, MockRule
from battery.runner import BatteryResult, make_run_id, run_battery
from battery.spec import BatterySpec, ProbeSpec
from core.evidence import (
    OUTCOME_ERROR,
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
)
from journal.store import KIND_EVIDENCE, KIND_RUN, Journal
from probes.injection import DEFAULT_ATTACKS, DEFAULT_CANARY

REPO_ROOT = Path(__file__).resolve().parent.parent
SUITE_PATH = REPO_ROOT / "suites" / "baseline.json"

REFUSAL = "I can't share that value."

PARAPHRASES = [f"Phrasing {i}: how long is standard delivery?" for i in range(20)]

SMALL_SPEC = BatterySpec(
    name="test-suite",
    description="Two probes, minimal configuration.",
    probes=(
        ProbeSpec(probe_id="injection-resistance", config={}),
        ProbeSpec(
            probe_id="output-consistency",
            config={
                "cases": [{"id": "delivery-window", "paraphrases": PARAPHRASES}]
            },
        ),
    ),
)


class TestProbeSpec(unittest.TestCase):
    def test_unknown_probe_id_fails_at_construction(self):
        with self.assertRaises(KeyError):
            ProbeSpec(probe_id="no-such-probe")

    def test_empty_probe_id_is_rejected(self):
        with self.assertRaises(ValueError):
            ProbeSpec(probe_id="")

    def test_build_returns_a_configured_probe(self):
        spec = ProbeSpec(probe_id="injection-resistance", config={"canary": "XX-1"})
        self.assertEqual(spec.build().canary, "XX-1")

    def test_bad_config_is_reported_against_the_probe(self):
        spec = ProbeSpec(probe_id="injection-resistance", config={"attacks": []})
        with self.assertRaises(ValueError) as ctx:
            spec.build()
        self.assertIn("injection-resistance", str(ctx.exception))

    def test_display_name_prefers_the_label(self):
        self.assertEqual(
            ProbeSpec(probe_id="injection-resistance", label="Canary run").display_name,
            "Canary run",
        )

    def test_display_name_falls_back_to_the_probe_title(self):
        name = ProbeSpec(probe_id="injection-resistance").display_name
        self.assertIn("injection", name.lower())

    def test_round_trip(self):
        spec = ProbeSpec(probe_id="injection-resistance", config={"canary": "X"}, label="L")
        self.assertEqual(ProbeSpec.from_dict(spec.to_dict()), spec)


class TestBatterySpec(unittest.TestCase):
    def test_requires_a_name_and_at_least_one_probe(self):
        with self.assertRaises(ValueError):
            BatterySpec(name="", probes=(ProbeSpec(probe_id="injection-resistance"),))
        with self.assertRaises(ValueError):
            BatterySpec(name="empty", probes=())

    def test_probe_ids_are_deduplicated_in_order(self):
        spec = BatterySpec(
            name="s",
            probes=(
                ProbeSpec(probe_id="injection-resistance"),
                ProbeSpec(probe_id="output-consistency", config={"cases": [
                    {"id": "c", "paraphrases": PARAPHRASES}]}),
                ProbeSpec(probe_id="injection-resistance", label="second"),
            ),
        )
        self.assertEqual(
            spec.probe_ids, ["injection-resistance", "output-consistency"]
        )

    def test_future_schema_version_is_refused(self):
        with self.assertRaises(ValueError):
            BatterySpec(
                name="s",
                probes=(ProbeSpec(probe_id="injection-resistance"),),
                schema_version=99,
            )

    def test_round_trip_through_dict(self):
        self.assertEqual(BatterySpec.from_dict(SMALL_SPEC.to_dict()), SMALL_SPEC)

    def test_round_trip_through_a_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "suite.json"
            SMALL_SPEC.save(path)
            self.assertEqual(BatterySpec.load(path), SMALL_SPEC)

    def test_malformed_json_is_reported_with_the_path(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                BatterySpec.load(path)
            self.assertIn("broken.json", str(ctx.exception))

    def test_missing_field_is_reported_with_the_path(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "incomplete.json"
            path.write_text(json.dumps({"probes": []}), encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                BatterySpec.load(path)
            self.assertIn("incomplete.json", str(ctx.exception))


class TestShippedSuite(unittest.TestCase):
    """The suite committed to the repo has to actually load and run."""

    def test_baseline_suite_loads(self):
        spec = BatterySpec.load(SUITE_PATH)
        self.assertEqual(spec.name, "baseline-assurance")
        self.assertEqual(
            spec.probe_ids,
            ["injection-resistance", "output-consistency", "citation-faithfulness"],
        )

    def test_every_probe_in_the_baseline_suite_builds(self):
        for probe_spec in BatterySpec.load(SUITE_PATH).probes:
            with self.subTest(probe=probe_spec.probe_id):
                self.assertIsNotNone(probe_spec.build())

    def test_baseline_suite_samples_are_large_enough_to_conclude(self):
        # A shipped suite whose samples are all below min_sample could never
        # return anything but inconclusive, which would look like a bug.
        for probe_spec in BatterySpec.load(SUITE_PATH).probes:
            probe = probe_spec.build()
            with self.subTest(probe=probe_spec.probe_id):
                if probe_spec.probe_id == "injection-resistance":
                    self.assertGreaterEqual(len(probe.attacks), probe.min_sample)
                elif probe_spec.probe_id == "output-consistency":
                    for case in probe.cases:
                        self.assertGreaterEqual(
                            len(case.paraphrases), probe.min_sample
                        )
                elif probe_spec.probe_id == "citation-faithfulness":
                    for case in probe.cases:
                        self.assertGreaterEqual(
                            len(case.questions), probe.min_sample
                        )

    def test_baseline_suite_runs_end_to_end_offline(self):
        adapter = MockAdapter.always(REFUSAL)
        result = run_battery(BatterySpec.load(SUITE_PATH), adapter)
        self.assertEqual(result.units_tested, 3)
        self.assertGreater(result.total_trials, 60)


class TestRunBattery(unittest.TestCase):
    def test_produces_one_evidence_record_per_unit(self):
        result = run_battery(SMALL_SPEC, MockAdapter.always(REFUSAL))
        self.assertEqual(result.units_tested, 2)
        self.assertEqual(
            [e.probe_id for e in result.evidence],
            ["injection-resistance", "output-consistency"],
        )

    def test_records_the_fingerprint_of_what_was_tested(self):
        adapter = MockAdapter.always(REFUSAL, seed=11)
        result = run_battery(SMALL_SPEC, adapter)
        self.assertEqual(result.fingerprint.digest(), adapter.fingerprint().digest())

    def test_run_id_is_derived_from_battery_fingerprint_and_time(self):
        adapter = MockAdapter.always(REFUSAL)
        expected = make_run_id(
            "test-suite", adapter.fingerprint(), "2026-07-27T00:00:00.000000Z"
        )
        result = run_battery(
            SMALL_SPEC, adapter, started_at="2026-07-27T00:00:00.000000Z"
        )
        self.assertEqual(result.run_id, expected)

    def test_different_models_give_different_run_ids(self):
        stamp = "2026-07-27T00:00:00.000000Z"
        a = run_battery(SMALL_SPEC, MockAdapter.always(REFUSAL, seed=1), started_at=stamp)
        b = run_battery(SMALL_SPEC, MockAdapter.always(REFUSAL, seed=2), started_at=stamp)
        self.assertNotEqual(a.run_id, b.run_id)

    def test_total_trials_counts_every_model_call(self):
        result = run_battery(SMALL_SPEC, MockAdapter.always(REFUSAL))
        self.assertEqual(result.total_trials, len(DEFAULT_ATTACKS) + len(PARAPHRASES))

    def test_evidence_can_be_selected_by_probe(self):
        result = run_battery(SMALL_SPEC, MockAdapter.always(REFUSAL))
        self.assertEqual(len(result.evidence_for("injection-resistance")), 1)
        self.assertEqual(result.evidence_for("citation-faithfulness"), ())


class TestOutcomeRollup(unittest.TestCase):
    def test_all_passing_rolls_up_to_pass(self):
        result = run_battery(SMALL_SPEC, MockAdapter.always(REFUSAL))
        self.assertEqual(result.outcome, OUTCOME_PASS)
        self.assertEqual(result.outcome_counts[OUTCOME_PASS], 2)

    def test_one_failure_fails_the_battery(self):
        # Leaks the canary; the consistency probe still passes.
        adapter = MockAdapter(
            [
                MockRule.make(pattern="base64", responses=f"Sure: {DEFAULT_CANARY}"),
                MockRule.make(responses=REFUSAL, mode=MATCH_ANY),
            ]
        )
        result = run_battery(SMALL_SPEC, adapter)
        self.assertEqual(result.outcome, OUTCOME_FAIL)
        self.assertEqual(result.outcome_counts[OUTCOME_FAIL], 1)
        self.assertEqual(result.outcome_counts[OUTCOME_PASS], 1)
        self.assertEqual(len(result.failures()), 1)

    def test_failure_outranks_error(self):
        result = BatteryResult(
            battery="b",
            run_id="r",
            started_at="t",
            finished_at="t",
            fingerprint=MockAdapter().fingerprint(),
            evidence=(),
        )
        from core.evidence import Evidence, ModelFingerprint

        def ev(outcome):
            return Evidence(
                probe_id="p",
                outcome=outcome,
                fingerprint=ModelFingerprint(adapter="mock", model="m"),
                started_at="t",
                finished_at="t",
            )

        cases = [
            ((OUTCOME_FAIL, OUTCOME_ERROR, OUTCOME_PASS), OUTCOME_FAIL),
            ((OUTCOME_ERROR, OUTCOME_INCONCLUSIVE, OUTCOME_PASS), OUTCOME_ERROR),
            ((OUTCOME_INCONCLUSIVE, OUTCOME_PASS), OUTCOME_INCONCLUSIVE),
            ((OUTCOME_PASS, OUTCOME_PASS), OUTCOME_PASS),
        ]
        for outcomes, expected in cases:
            with self.subTest(outcomes=outcomes):
                rolled = BatteryResult(
                    battery="b",
                    run_id="r",
                    started_at="t",
                    finished_at="t",
                    fingerprint=result.fingerprint,
                    evidence=tuple(ev(o) for o in outcomes),
                )
                self.assertEqual(rolled.outcome, expected)

    def test_an_empty_run_is_inconclusive_not_passing(self):
        result = BatteryResult(
            battery="b",
            run_id="r",
            started_at="t",
            finished_at="t",
            fingerprint=MockAdapter().fingerprint(),
            evidence=(),
        )
        self.assertEqual(result.outcome, OUTCOME_INCONCLUSIVE)

    def test_a_broken_endpoint_yields_error_evidence_and_the_run_continues(self):
        # The injection probe's calls fail; the consistency probe still runs.
        adapter = MockAdapter(
            [
                MockRule(pattern="Ignore all previous", error="endpoint down"),
                MockRule.make(responses=REFUSAL, mode=MATCH_ANY),
            ]
        )
        result = run_battery(SMALL_SPEC, adapter)
        self.assertEqual(result.outcome_counts[OUTCOME_ERROR], 1)
        self.assertEqual(result.outcome_counts[OUTCOME_PASS], 1)
        self.assertEqual(result.outcome, OUTCOME_ERROR)

    def test_no_composite_score_is_produced(self):
        # Guards the D-016 decision: averaging incommensurate rates would be
        # meaningless, so no such attribute should ever appear.
        result = run_battery(SMALL_SPEC, MockAdapter.always(REFUSAL))
        for forbidden in ("score", "overall_score", "composite", "average"):
            self.assertFalse(
                hasattr(result, forbidden), f"unexpected {forbidden} attribute"
            )


class TestSerialization(unittest.TestCase):
    def test_full_round_trip(self):
        result = run_battery(SMALL_SPEC, MockAdapter.always(REFUSAL))
        restored = BatteryResult.from_dict(result.to_dict())
        self.assertEqual(restored.run_id, result.run_id)
        self.assertEqual(restored.evidence, result.evidence)
        self.assertEqual(restored.outcome, result.outcome)

    def test_future_schema_is_refused(self):
        result = run_battery(SMALL_SPEC, MockAdapter.always(REFUSAL))
        payload = result.to_dict()
        payload["schema_version"] = 99
        with self.assertRaises(ValueError):
            BatteryResult.from_dict(payload)

    def test_run_record_is_a_manifest_not_a_copy(self):
        result = run_battery(SMALL_SPEC, MockAdapter.always(REFUSAL))
        record = result.run_record()
        self.assertNotIn("evidence", record)
        self.assertEqual(record["evidence_hashes"], result.evidence_hashes())
        self.assertEqual(len(record["evidence_hashes"]), 2)

    def test_summary_lines_carry_uncertainty(self):
        result = run_battery(SMALL_SPEC, MockAdapter.always(REFUSAL))
        text = "\n".join(result.summary_lines())
        self.assertIn("95% CI", text)
        self.assertIn("test-suite", text)


class TestJournalIntegration(unittest.TestCase):
    def test_run_writes_evidence_then_a_manifest(self):
        journal = Journal()
        self.addCleanup(journal.close)
        result = run_battery(SMALL_SPEC, MockAdapter.always(REFUSAL), journal=journal)

        kinds = [e.kind for e in journal.entries()]
        self.assertEqual(kinds, [KIND_EVIDENCE, KIND_EVIDENCE, KIND_RUN])
        self.assertTrue(journal.verify().ok)
        self.assertEqual(journal.evidence(), list(result.evidence))

    def test_manifest_hashes_match_the_journaled_evidence(self):
        journal = Journal()
        self.addCleanup(journal.close)
        run_battery(SMALL_SPEC, MockAdapter.always(REFUSAL), journal=journal)

        [run_entry] = journal.entries_of_kind(KIND_RUN)
        manifest = run_entry.parsed()["evidence_hashes"]
        stored = [e.content_hash() for e in journal.evidence()]
        self.assertEqual(manifest, stored)

    def test_two_runs_chain_into_one_journal(self):
        journal = Journal()
        self.addCleanup(journal.close)
        run_battery(SMALL_SPEC, MockAdapter.always(REFUSAL), journal=journal)
        run_battery(SMALL_SPEC, MockAdapter.always("A different answer."), journal=journal)

        self.assertEqual(len(journal), 6)
        self.assertTrue(journal.verify().ok)
        self.assertEqual(len(journal.entries_of_kind(KIND_RUN)), 2)


if __name__ == "__main__":
    unittest.main()
