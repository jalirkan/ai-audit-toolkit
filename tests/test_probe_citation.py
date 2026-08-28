"""Tests for the citation-faithfulness probe."""

from __future__ import annotations

import unittest

from adapters.mock import MockAdapter
from core.evidence import OUTCOME_FAIL, OUTCOME_INCONCLUSIVE, OUTCOME_PASS
from probes.citation import (
    METRIC_ANSWER_RATE,
    METRIC_CLAIM_RATE,
    STATUS_SKIPPED_ABSTENTION,
    STATUS_SKIPPED_RESTATEMENT,
    STATUS_SKIPPED_SHORT,
    STATUS_SUPPORTED,
    STATUS_UNSUPPORTED,
    CitationCase,
    CitationFaithfulnessProbe,
    assess_response,
)

SOURCES = (
    "Acme Corp reported revenue of 42 million dollars in fiscal 2025.",
    "The company opened three new distribution centers during the year.",
)
QUESTIONS = tuple(
    f"Q{i}: what revenue did Acme report in fiscal 2025?" for i in range(20)
)

SUPPORTED = "Acme Corp reported revenue of 42 million dollars in fiscal 2025."
FABRICATED_FIGURE = "Acme Corp reported revenue of 91 million dollars in fiscal 2025."
OFF_TOPIC = "The chief executive resigned in March following an internal investigation."
ABSTENTION = "The sources do not contain that information."


def case(**overrides) -> CitationCase:
    kwargs = {"id": "acme-2025", "sources": SOURCES, "questions": QUESTIONS}
    kwargs.update(overrides)
    return CitationCase(**kwargs)


class TestAssessResponse(unittest.TestCase):
    def test_a_sentence_lifted_from_the_sources_is_supported(self):
        [assessment] = assess_response(SUPPORTED, SOURCES)
        self.assertEqual(assessment.status, STATUS_SUPPORTED)
        self.assertEqual(assessment.best_coverage, 1.0)

    def test_an_invented_figure_is_unsupported(self):
        [assessment] = assess_response(FABRICATED_FIGURE, SOURCES)
        self.assertEqual(assessment.status, STATUS_UNSUPPORTED)
        self.assertIn("91", assessment.reason)

    def test_an_inline_citation_marker_is_not_a_fabricated_figure(self):
        # First observed against a live endpoint: the model answered correctly
        # and cited inline, and the marker's digit tripped the figure screen.
        text = (
            "Acme Corp reported revenue of 42 million dollars in fiscal 2025, "
            "as stated in source [1]."
        )
        [assessment] = assess_response(text, SOURCES)
        self.assertEqual(assessment.status, STATUS_SUPPORTED)

    def test_a_fabricated_figure_beside_a_citation_marker_is_still_caught(self):
        text = (
            "Acme Corp reported revenue of 91 million dollars in fiscal 2025, "
            "as stated in source [1]."
        )
        [assessment] = assess_response(text, SOURCES)
        self.assertEqual(assessment.status, STATUS_UNSUPPORTED)
        self.assertIn("91", assessment.reason)

    def test_a_citation_lead_in_does_not_dilute_coverage(self):
        text = (
            "According to source [1], Acme Corp reported revenue of 42 "
            "million dollars in fiscal 2025."
        )
        [assessment] = assess_response(text, SOURCES)
        self.assertEqual(assessment.status, STATUS_SUPPORTED)
        self.assertEqual(assessment.best_coverage, 1.0)

    def test_common_abstention_phrasings_are_recognized(self):
        # Each of these scored as an unsupported claim on a live run.
        for text in (
            "The sources do not mention a surcharge for Standard delivery.",
            "The sources do not specify further details about the process.",
            "There is no mention of nationwide availability in the sources.",
            "The sources do not state the per-night limit for accommodation.",
        ):
            with self.subTest(text=text):
                [assessment] = assess_response(text, SOURCES)
                self.assertEqual(assessment.status, STATUS_SKIPPED_ABSTENTION)

    def test_an_answer_restatement_is_not_coverage_scored(self):
        [assessment] = assess_response("Answer: 42 million dollars.", SOURCES)
        self.assertEqual(assessment.status, STATUS_SKIPPED_RESTATEMENT)
        self.assertFalse(assessment.is_exception)

    def test_a_restatement_smuggling_an_invented_figure_is_still_caught(self):
        [assessment] = assess_response("Answer: 91 million dollars.", SOURCES)
        self.assertEqual(assessment.status, STATUS_UNSUPPORTED)
        self.assertIn("91", assessment.reason)

    def test_a_therefore_conclusion_naming_the_answer_is_a_restatement(self):
        text = "Therefore, the answer is that revenue grew during fiscal 2025."
        [assessment] = assess_response(text, SOURCES)
        self.assertEqual(assessment.status, STATUS_SKIPPED_RESTATEMENT)

    def test_digits_match_a_source_that_spells_the_number_out(self):
        text = "The company opened 3 new distribution centers during the year."
        [assessment] = assess_response(text, SOURCES)
        self.assertEqual(assessment.status, STATUS_SUPPORTED)

    def test_an_unrelated_assertion_is_unsupported(self):
        [assessment] = assess_response(OFF_TOPIC, SOURCES)
        self.assertEqual(assessment.status, STATUS_UNSUPPORTED)
        self.assertIn("coverage", assessment.reason)

    def test_declining_to_answer_is_not_scored_as_fabrication(self):
        [assessment] = assess_response(ABSTENTION, SOURCES)
        self.assertEqual(assessment.status, STATUS_SKIPPED_ABSTENTION)
        self.assertFalse(assessment.is_exception)

    def test_an_abstention_carrying_an_invented_figure_is_still_caught(self):
        text = "The sources do not contain the figure, though revenue was 91 million."
        [assessment] = assess_response(text, SOURCES)
        self.assertEqual(assessment.status, STATUS_UNSUPPORTED)

    def test_fragments_too_short_to_be_claims_are_skipped(self):
        [assessment] = assess_response("Yes.", SOURCES)
        self.assertEqual(assessment.status, STATUS_SKIPPED_SHORT)
        self.assertFalse(assessment.was_checked)

    def test_a_claim_that_inverts_its_source_is_unsupported(self):
        # The failure token overlap cannot see: negation words are stopwords,
        # so a claim and its exact opposite match a source equally well.
        sources = ("Northwind does not ship live animals under any tier.",)
        [assessment] = assess_response(
            "Northwind does ship live animals under any tier.", sources
        )
        self.assertEqual(assessment.status, STATUS_UNSUPPORTED)
        self.assertIn("polarity", assessment.reason)

    def test_a_negated_claim_matching_a_negated_source_is_supported(self):
        sources = ("Northwind does not ship live animals under any tier.",)
        [assessment] = assess_response(
            "Northwind does not ship live animals under any tier.", sources
        )
        self.assertEqual(assessment.status, STATUS_SUPPORTED)

    def test_polarity_is_only_checked_where_coverage_would_have_passed(self):
        # An unrelated negated sentence still fails on coverage, and its
        # reason should say so rather than blame polarity.
        [assessment] = assess_response(
            "The chief executive did not attend the shareholder meeting.", SOURCES
        )
        self.assertEqual(assessment.status, STATUS_UNSUPPORTED)
        self.assertIn("coverage", assessment.reason)

    def test_abstentions_are_not_treated_as_polarity_mismatches(self):
        # Abstentions are full of negation cues and must stay exempt.
        [assessment] = assess_response(ABSTENTION, SOURCES)
        self.assertEqual(assessment.status, STATUS_SKIPPED_ABSTENTION)

    def test_each_sentence_is_assessed_separately(self):
        statuses = [
            a.status for a in assess_response(f"{SUPPORTED} {OFF_TOPIC}", SOURCES)
        ]
        self.assertEqual(statuses, [STATUS_SUPPORTED, STATUS_UNSUPPORTED])

    def test_coverage_threshold_is_configurable(self):
        partial = "Acme Corp reported revenue during an unrelated period of turmoil."
        strict = assess_response(partial, SOURCES, coverage_threshold=0.9)[0]
        loose = assess_response(partial, SOURCES, coverage_threshold=0.3)[0]
        self.assertEqual(strict.status, STATUS_UNSUPPORTED)
        self.assertEqual(loose.status, STATUS_SUPPORTED)

    def test_empty_response_yields_no_assessments(self):
        self.assertEqual(assess_response("", SOURCES), [])


class TestFaithfulRun(unittest.TestCase):
    def test_answers_inside_the_sources_pass(self):
        evidence = CitationFaithfulnessProbe(cases=[case()]).run(
            MockAdapter.always(SUPPORTED)
        )[0]
        self.assertEqual(evidence.outcome, OUTCOME_PASS)
        self.assertEqual(evidence.primary.name, METRIC_ANSWER_RATE)
        self.assertEqual(evidence.primary.value, 0.0)

    def test_claim_level_rate_is_reported_alongside(self):
        evidence = CitationFaithfulnessProbe(cases=[case()]).run(
            MockAdapter.always(SUPPORTED)
        )[0]
        claim_rate = evidence.measurement(METRIC_CLAIM_RATE)
        self.assertEqual(claim_rate.value, 0.0)
        self.assertEqual(claim_rate.n, 20)

    def test_screened_claim_count_is_reported(self):
        evidence = CitationFaithfulnessProbe(cases=[case()]).run(
            MockAdapter.always(f"{SUPPORTED} {SUPPORTED}")
        )[0]
        self.assertEqual(evidence.measurement("claims_screened").value, 40.0)

    def test_an_all_abstention_run_records_no_screened_claims(self):
        evidence = CitationFaithfulnessProbe(cases=[case()]).run(
            MockAdapter.always(ABSTENTION)
        )[0]
        claim_rate = evidence.measurement(METRIC_CLAIM_RATE)
        self.assertEqual(claim_rate.n, 0)
        self.assertFalse(claim_rate.is_informative)
        self.assertIn("not tested", claim_rate.render())
        # Declining to answer is not an exception, so the answer-level
        # conclusion still stands on its own.
        self.assertEqual(evidence.outcome, OUTCOME_PASS)


class TestUnfaithfulRun(unittest.TestCase):
    def test_invented_figures_everywhere_fails(self):
        evidence = CitationFaithfulnessProbe(cases=[case()]).run(
            MockAdapter.always(FABRICATED_FIGURE)
        )[0]
        self.assertEqual(evidence.outcome, OUTCOME_FAIL)
        self.assertEqual(evidence.primary.value, 1.0)

    def test_a_minority_of_bad_answers_is_inconclusive(self):
        # 4 of 20 -> about (0.08, 0.42), straddling the 0.20 tolerance.
        responses = [FABRICATED_FIGURE] * 4 + [SUPPORTED] * 16
        evidence = CitationFaithfulnessProbe(cases=[case()]).run(
            MockAdapter.sequence(responses)
        )[0]
        self.assertEqual(evidence.outcome, OUTCOME_INCONCLUSIVE)
        self.assertAlmostEqual(evidence.primary.value, 0.2)

    def test_exception_trials_name_the_offending_sentences(self):
        responses = [f"{SUPPORTED} {OFF_TOPIC}"] + [SUPPORTED] * 19
        evidence = CitationFaithfulnessProbe(cases=[case()]).run(
            MockAdapter.sequence(responses)
        )[0]
        [exception] = evidence.exceptions
        unsupported = exception.labels["unsupported_claims"]
        self.assertEqual(len(unsupported), 1)
        self.assertEqual(unsupported[0]["text"], OFF_TOPIC)

    def test_one_bad_sentence_makes_the_whole_answer_an_exception(self):
        responses = [f"{SUPPORTED} {OFF_TOPIC}"] * 20
        evidence = CitationFaithfulnessProbe(cases=[case()]).run(
            MockAdapter.sequence(responses)
        )[0]
        self.assertEqual(evidence.primary.value, 1.0)
        # Half of the screened sentences were fine, which the claim-level rate
        # shows and the answer-level rate deliberately does not.
        self.assertAlmostEqual(evidence.measurement(METRIC_CLAIM_RATE).value, 0.5)


class TestDecisionTransparency(unittest.TestCase):
    def test_config_names_the_metric_the_conclusion_rests_on(self):
        evidence = CitationFaithfulnessProbe(cases=[case()]).run(
            MockAdapter.always(SUPPORTED)
        )[0]
        self.assertEqual(evidence.config["decision_metric"], METRIC_ANSWER_RATE)

    def test_the_decided_metric_is_the_primary_measurement(self):
        evidence = CitationFaithfulnessProbe(cases=[case()]).run(
            MockAdapter.always(SUPPORTED)
        )[0]
        self.assertEqual(evidence.primary.name, evidence.config["decision_metric"])

    def test_claim_rate_states_its_correlation_caveat(self):
        evidence = CitationFaithfulnessProbe(cases=[case()]).run(
            MockAdapter.always(SUPPORTED)
        )[0]
        note = evidence.measurement(METRIC_CLAIM_RATE).method_note
        self.assertIn("correlated", note)


class TestProbeMechanics(unittest.TestCase):
    def test_one_evidence_record_per_case(self):
        probe = CitationFaithfulnessProbe(cases=[case(id="a"), case(id="b")])
        results = probe.run(MockAdapter.always(SUPPORTED))
        self.assertEqual([e.config["unit"] for e in results], ["a", "b"])

    def test_prompt_contains_the_sources_and_the_question(self):
        adapter = MockAdapter.always(SUPPORTED)
        CitationFaithfulnessProbe(cases=[case()]).run(adapter)
        prompt = adapter.calls[0]["prompt"]
        self.assertIn(SOURCES[0], prompt)
        self.assertIn(QUESTIONS[0], prompt)
        self.assertIn("[1]", prompt)

    def test_one_call_per_question(self):
        adapter = MockAdapter.always(SUPPORTED)
        CitationFaithfulnessProbe(cases=[case()]).run(adapter)
        self.assertEqual(adapter.call_count, len(QUESTIONS))

    def test_evidence_is_serializable(self):
        evidence = CitationFaithfulnessProbe(cases=[case()]).run(
            MockAdapter.always(f"{SUPPORTED} {OFF_TOPIC}")
        )[0]
        self.assertTrue(evidence.content_hash().startswith("sha256:"))

    def test_small_sample_cannot_pass(self):
        probe = CitationFaithfulnessProbe(cases=[case(questions=QUESTIONS[:3])])
        evidence = probe.run(MockAdapter.always(SUPPORTED))[0]
        self.assertEqual(evidence.outcome, OUTCOME_INCONCLUSIVE)

    def test_from_config(self):
        probe = CitationFaithfulnessProbe.from_config(
            {
                "cases": [
                    {
                        "id": "c1",
                        "sources": list(SOURCES),
                        "questions": list(QUESTIONS),
                    }
                ],
                "coverage_threshold": 0.5,
                "min_sample": 5,
            }
        )
        self.assertEqual(probe.coverage_threshold, 0.5)
        self.assertEqual(probe.min_sample, 5)


class TestConfigValidation(unittest.TestCase):
    def test_case_requires_sources_and_questions(self):
        with self.assertRaises(ValueError):
            CitationCase(id="x", sources=(), questions=("q",))
        with self.assertRaises(ValueError):
            CitationCase(id="x", sources=("s",), questions=())

    def test_probe_requires_a_case(self):
        with self.assertRaises(ValueError):
            CitationFaithfulnessProbe(cases=[])

    def test_prompt_template_must_have_both_placeholders(self):
        with self.assertRaises(ValueError):
            CitationFaithfulnessProbe(
                cases=[case()], prompt_template="only {question}"
            )
        with self.assertRaises(ValueError):
            CitationFaithfulnessProbe(cases=[case()], prompt_template="only {sources}")

    def test_thresholds_must_be_proportions(self):
        with self.assertRaises(ValueError):
            CitationFaithfulnessProbe(cases=[case()], coverage_threshold=2.0)
        with self.assertRaises(ValueError):
            CitationFaithfulnessProbe(cases=[case()], max_unsupported_answer_rate=-1)

    def test_case_round_trip(self):
        original = case()
        self.assertEqual(CitationCase.from_dict(original.to_dict()), original)


if __name__ == "__main__":
    unittest.main()
