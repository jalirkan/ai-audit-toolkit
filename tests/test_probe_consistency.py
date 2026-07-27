"""Tests for the output-consistency probe.

Each scenario is a mock scripted to a known behaviour, so the expected outcome
is arithmetic rather than a guess: 20 identical answers must pass, 20 different
answers must fail, and 15 of 20 must be inconclusive because the interval
straddles the threshold.
"""

from __future__ import annotations

import unittest

from adapters.mock import MockAdapter
from core.evidence import OUTCOME_FAIL, OUTCOME_INCONCLUSIVE, OUTCOME_PASS
from probes.consistency import (
    METRIC_CONSENSUS,
    METRIC_EXPECTED,
    ConsistencyCase,
    ConsistencyProbe,
)

PARAPHRASES = tuple(
    f"Phrasing {i}: what is the capital city of France?" for i in range(20)
)

CONSENSUS_ANSWER = "The capital of France is Paris."
DIVERGENT = tuple(f"alpha{i} beta{i} gamma{i} delta{i}" for i in range(20))


def case(**overrides) -> ConsistencyCase:
    kwargs = {"id": "capital-of-france", "paraphrases": PARAPHRASES}
    kwargs.update(overrides)
    return ConsistencyCase(**kwargs)


class TestConsensusMode(unittest.TestCase):
    def test_identical_answers_pass(self):
        probe = ConsistencyProbe(cases=[case()])
        evidence = probe.run(MockAdapter.always(CONSENSUS_ANSWER))[0]
        self.assertEqual(evidence.outcome, OUTCOME_PASS)
        self.assertEqual(evidence.primary.name, METRIC_CONSENSUS)
        self.assertEqual(evidence.primary.value, 1.0)
        self.assertEqual(evidence.primary.n, 20)

    def test_all_different_answers_fail(self):
        probe = ConsistencyProbe(cases=[case()])
        evidence = probe.run(MockAdapter.sequence(DIVERGENT))[0]
        self.assertEqual(evidence.outcome, OUTCOME_FAIL)
        self.assertAlmostEqual(evidence.primary.value, 0.05)

    def test_partial_agreement_is_inconclusive(self):
        # 15 of 20 -> about (0.53, 0.89), straddling the 0.80 requirement.
        responses = [CONSENSUS_ANSWER] * 15 + list(DIVERGENT[:5])
        evidence = ConsistencyProbe(cases=[case()]).run(
            MockAdapter.sequence(responses)
        )[0]
        self.assertEqual(evidence.outcome, OUTCOME_INCONCLUSIVE)
        self.assertAlmostEqual(evidence.primary.value, 0.75)

    def test_cluster_count_is_reported(self):
        responses = [CONSENSUS_ANSWER] * 15 + list(DIVERGENT[:5])
        evidence = ConsistencyProbe(cases=[case()]).run(
            MockAdapter.sequence(responses)
        )[0]
        clusters = evidence.measurement("distinct_answer_clusters")
        self.assertEqual(clusters.value, 6.0)

    def test_full_agreement_is_a_single_cluster(self):
        evidence = ConsistencyProbe(cases=[case()]).run(
            MockAdapter.always(CONSENSUS_ANSWER)
        )[0]
        self.assertEqual(evidence.measurement("distinct_answer_clusters").value, 1.0)

    def test_exceptions_are_the_dissenting_phrasings(self):
        responses = [CONSENSUS_ANSWER] * 15 + list(DIVERGENT[:5])
        evidence = ConsistencyProbe(cases=[case()]).run(
            MockAdapter.sequence(responses)
        )[0]
        self.assertEqual([t.index for t in evidence.exceptions], [15, 16, 17, 18, 19])

    def test_similarity_threshold_changes_grouping(self):
        # Same answer with one word swapped: strict grouping splits it, loose
        # grouping keeps it together.
        responses = ["capital france paris city"] * 10 + [
            "capital france lyon city"
        ] * 10
        strict = ConsistencyProbe(cases=[case()], similarity_threshold=0.95).run(
            MockAdapter.sequence(responses)
        )[0]
        loose = ConsistencyProbe(cases=[case()], similarity_threshold=0.3).run(
            MockAdapter.sequence(responses)
        )[0]
        self.assertAlmostEqual(strict.primary.value, 0.5)
        self.assertAlmostEqual(loose.primary.value, 1.0)


class TestAnswerKeyMode(unittest.TestCase):
    def test_expected_answer_present_everywhere_passes(self):
        probe = ConsistencyProbe(cases=[case(expected_any=("Paris",))])
        evidence = probe.run(MockAdapter.always("The capital is Paris."))[0]
        self.assertEqual(evidence.outcome, OUTCOME_PASS)
        self.assertEqual(evidence.primary.name, METRIC_EXPECTED)

    def test_expected_answer_absent_fails(self):
        probe = ConsistencyProbe(cases=[case(expected_any=("Paris",))])
        evidence = probe.run(MockAdapter.always("The capital is Lyon."))[0]
        self.assertEqual(evidence.outcome, OUTCOME_FAIL)
        self.assertEqual(evidence.primary.value, 0.0)

    def test_matching_is_case_insensitive(self):
        probe = ConsistencyProbe(cases=[case(expected_any=("paris",))])
        evidence = probe.run(MockAdapter.always("PARIS is the capital."))[0]
        self.assertEqual(evidence.primary.value, 1.0)

    def test_any_of_several_expected_answers_counts(self):
        probe = ConsistencyProbe(cases=[case(expected_any=("Paris", "City of Light"))])
        evidence = probe.run(MockAdapter.always("It is the City of Light."))[0]
        self.assertEqual(evidence.primary.value, 1.0)

    def test_answer_key_mode_is_recorded_in_config(self):
        probe = ConsistencyProbe(cases=[case(expected_any=("Paris",))])
        evidence = probe.run(MockAdapter.always("Paris."))[0]
        self.assertEqual(evidence.config["mode"], "answer-key")

    def test_consensus_mode_is_recorded_in_config(self):
        evidence = ConsistencyProbe(cases=[case()]).run(MockAdapter.always("x y z"))[0]
        self.assertEqual(evidence.config["mode"], "consensus")

    def test_answer_key_mode_reports_no_cluster_count(self):
        probe = ConsistencyProbe(cases=[case(expected_any=("Paris",))])
        evidence = probe.run(MockAdapter.always("Paris."))[0]
        self.assertIsNone(evidence.measurement("distinct_answer_clusters"))


class TestProbeMechanics(unittest.TestCase):
    def test_one_evidence_record_per_case(self):
        probe = ConsistencyProbe(
            cases=[case(id="first"), case(id="second")]
        )
        results = probe.run(MockAdapter.always(CONSENSUS_ANSWER))
        self.assertEqual([e.config["unit"] for e in results], ["first", "second"])

    def test_every_paraphrase_is_submitted_once(self):
        adapter = MockAdapter.always(CONSENSUS_ANSWER)
        ConsistencyProbe(cases=[case()]).run(adapter)
        self.assertEqual(adapter.prompts_seen(), list(PARAPHRASES))

    def test_case_system_prompt_is_used_and_recorded(self):
        adapter = MockAdapter.always(CONSENSUS_ANSWER)
        probe = ConsistencyProbe(cases=[case(system="Answer in one word.")])
        evidence = probe.run(adapter)[0]
        self.assertTrue(all(t.system == "Answer in one word." for t in evidence.trials))

    def test_trials_record_prompts_and_responses(self):
        evidence = ConsistencyProbe(cases=[case()]).run(
            MockAdapter.always(CONSENSUS_ANSWER)
        )[0]
        self.assertEqual(len(evidence.trials), 20)
        self.assertEqual(evidence.trials[0].prompt, PARAPHRASES[0])
        self.assertEqual(evidence.trials[0].response_text, CONSENSUS_ANSWER)

    def test_evidence_is_serializable(self):
        evidence = ConsistencyProbe(cases=[case()]).run(
            MockAdapter.always(CONSENSUS_ANSWER)
        )[0]
        self.assertTrue(evidence.content_hash().startswith("sha256:"))

    def test_from_config_round_trip(self):
        probe = ConsistencyProbe.from_config(
            {
                "cases": [
                    {
                        "id": "c1",
                        "paraphrases": list(PARAPHRASES),
                        "expected_any": ["Paris"],
                    }
                ],
                "min_agreement": 0.9,
                "min_sample": 10,
            }
        )
        self.assertEqual(probe.min_agreement, 0.9)
        self.assertEqual(probe.min_sample, 10)
        self.assertTrue(probe.cases[0].uses_answer_key)

    def test_small_sample_cannot_pass_even_with_perfect_agreement(self):
        probe = ConsistencyProbe(
            cases=[case(paraphrases=("a?", "b?", "c?"))], min_sample=20
        )
        evidence = probe.run(MockAdapter.always(CONSENSUS_ANSWER))[0]
        self.assertEqual(evidence.outcome, OUTCOME_INCONCLUSIVE)


class TestConfigValidation(unittest.TestCase):
    def test_case_needs_at_least_two_paraphrases(self):
        with self.assertRaises(ValueError):
            ConsistencyCase(id="x", paraphrases=("only one?",))

    def test_case_needs_an_id(self):
        with self.assertRaises(ValueError):
            ConsistencyCase(id="", paraphrases=("a?", "b?"))

    def test_probe_needs_at_least_one_case(self):
        with self.assertRaises(ValueError):
            ConsistencyProbe(cases=[])

    def test_thresholds_must_be_proportions(self):
        with self.assertRaises(ValueError):
            ConsistencyProbe(cases=[case()], similarity_threshold=1.5)
        with self.assertRaises(ValueError):
            ConsistencyProbe(cases=[case()], min_agreement=-0.1)

    def test_case_serialization_round_trip(self):
        original = case(expected_any=("Paris",), system="terse")
        self.assertEqual(ConsistencyCase.from_dict(original.to_dict()), original)


if __name__ == "__main__":
    unittest.main()
