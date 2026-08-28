"""Tests for the golden RAG dataset and screen-check harness."""

from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from adapters.mock import MATCH_ANY, MockAdapter, MockRule
from core.evidence import OUTCOME_FAIL, OUTCOME_PASS
from probes.citation import CitationCase
from rag.dataset import (
    EXPECT_FAITHFUL,
    EXPECT_UNFAITHFUL,
    GoldenDataset,
    GoldenItem,
    load_dataset,
)
from rag.harness import run_live, run_screen_check

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "datasets" / "northwind-rag-golden.json"

SOURCES = (
    "Northwind Logistics offers three service tiers: Standard, Priority, and Overnight.",
    "Standard delivery is quoted at five to seven business days within the contiguous United States.",
    "Priority delivery carries a surcharge of 18 dollars per shipment.",
)


def tiny_dataset(*items: GoldenItem) -> GoldenDataset:
    return GoldenDataset(id="tiny", sources=SOURCES, items=items)


class TestDatasetLoading(unittest.TestCase):
    def test_shipped_dataset_loads(self):
        ds = load_dataset(GOLDEN)
        self.assertEqual(ds.id, "northwind-rag-golden")
        self.assertGreaterEqual(len(ds.items), 20)
        self.assertTrue(ds.sources)

    def test_rejects_empty_sources(self):
        with self.assertRaises(ValueError):
            GoldenDataset(
                id="x",
                sources=(),
                items=(
                    GoldenItem("a", "q", "a", EXPECT_FAITHFUL),
                ),
            )

    def test_rejects_unknown_expect(self):
        with self.assertRaises(ValueError):
            GoldenItem("a", "q", "answer text here", "maybe")

    def test_rejects_duplicate_item_ids(self):
        item = GoldenItem("dup", "q", "Standard delivery is quoted at five to seven business days within the contiguous United States.", EXPECT_FAITHFUL)
        with self.assertRaises(ValueError):
            GoldenDataset(id="x", sources=SOURCES, items=(item, item))

    def test_rejects_future_schema(self):
        with self.assertRaises(ValueError):
            GoldenDataset.from_dict(
                {
                    "schema_version": 99,
                    "id": "x",
                    "sources": list(SOURCES),
                    "items": [
                        {
                            "id": "a",
                            "question": "q",
                            "gold_answer": "Standard delivery is quoted at five to seven business days within the contiguous United States.",
                            "expect": EXPECT_FAITHFUL,
                        }
                    ],
                }
            )

    def test_as_citation_case(self):
        ds = load_dataset(GOLDEN)
        case = ds.as_citation_case()
        self.assertIsInstance(case, CitationCase)
        self.assertEqual(case.id, ds.id)
        self.assertEqual(len(case.questions), len(ds.items))


class TestScreenCheck(unittest.TestCase):
    def test_faithful_gold_is_not_flagged(self):
        ds = tiny_dataset(
            GoldenItem(
                "ok",
                "How long is Standard?",
                "Standard delivery is quoted at five to seven business days within the contiguous United States.",
                EXPECT_FAITHFUL,
            )
        )
        result = run_screen_check(ds, min_sample=1, min_accuracy=0.5)
        self.assertTrue(result.items[0].correct)
        self.assertFalse(result.items[0].flagged_unfaithful)

    def test_unfaithful_gold_is_flagged(self):
        ds = tiny_dataset(
            GoldenItem(
                "bad",
                "Fee?",
                "Standard delivery carries a surcharge of 99 dollars per shipment.",
                EXPECT_UNFAITHFUL,
            )
        )
        result = run_screen_check(ds, min_sample=1, min_accuracy=0.5)
        self.assertTrue(result.items[0].correct)
        self.assertTrue(result.items[0].flagged_unfaithful)

    def test_accuracy_carries_an_interval(self):
        result = run_screen_check(load_dataset(GOLDEN))
        self.assertIsNotNone(result.accuracy.ci_low)
        rendered = result.accuracy.render()
        self.assertIn("95% CI", rendered)
        self.assertRegex(rendered, r"\d+/\d+")

    def test_shipped_golden_set_fails_on_the_screens_blind_spots(self):
        # This asserted a pass while the dataset contained only cases the
        # screen handles. The dataset now includes the failure modes
        # probes/citation.py documents, and the honest result is a fail.
        result = run_screen_check(load_dataset(GOLDEN))
        self.assertEqual(result.outcome, OUTCOME_FAIL)
        self.assertEqual(
            sorted(result.failing_categories),
            ["entity-swap", "paraphrase", "term-swap"],
        )

    def test_the_screen_is_perfect_on_the_categories_it_can_do(self):
        result = run_screen_check(load_dataset(GOLDEN))
        by_category = {s.category: s.accuracy.value for s in result.strata}
        for category in (
            "verbatim",
            "unsourced-number",
            "negation-flip",
            "abstention",
            "off-topic",
        ):
            with self.subTest(category=category):
                self.assertEqual(by_category[category], 1.0)

    def test_every_miss_was_predicted_by_the_dataset(self):
        # An unpredicted miss is new information about the screen; there
        # should be none, because the dataset labels its own blind spots.
        result = run_screen_check(load_dataset(GOLDEN))
        self.assertEqual([i.item_id for i in result.surprises], [])

    def test_the_overall_figure_is_labelled_composition_dependent(self):
        text = "\n".join(run_screen_check(load_dataset(GOLDEN)).summary_lines())
        self.assertIn("depends on the mix of cases", text)

    def test_deleting_the_hard_cases_would_flatter_the_screen(self):
        # The point of keeping them. Screening only the categories the method
        # handles yields a perfect score that says nothing.
        dataset = load_dataset(GOLDEN)
        easy = tuple(
            i
            for i in dataset.items
            if i.category not in ("paraphrase", "entity-swap", "term-swap")
        )
        flattered = run_screen_check(replace(dataset, items=easy))
        self.assertEqual(flattered.accuracy.value, 1.0)
        self.assertGreater(
            flattered.accuracy.value,
            run_screen_check(dataset).accuracy.value,
        )

    def test_a_wrong_label_fails_the_check(self):
        # Most gold answers are faithful but labeled unfaithful: screen disagrees.
        ds = tiny_dataset(
            *[
                GoldenItem(
                    f"mislabeled-{i}",
                    "How long is Standard?",
                    "Standard delivery is quoted at five to seven business days within the contiguous United States.",
                    EXPECT_UNFAITHFUL,
                )
                for i in range(20)
            ]
        )
        result = run_screen_check(ds, min_accuracy=0.9, min_sample=20)
        self.assertEqual(result.outcome, OUTCOME_FAIL)
        self.assertTrue(all(not item.correct for item in result.items))

    def test_confusion_counts_are_not_a_composite_score(self):
        result = run_screen_check(load_dataset(GOLDEN))
        for forbidden in ("score", "composite", "f1", "average"):
            self.assertFalse(hasattr(result, forbidden))

    def test_serializes(self):
        from core.canonical import canonical_json

        payload = run_screen_check(load_dataset(GOLDEN)).to_dict()
        self.assertTrue(canonical_json(payload))

    def test_result_names_the_probe_it_grades(self):
        """The dataset never mentions the probe, so the result must: without
        probe_id, the check and the probe it grades share no identifier."""
        from probes.citation import CitationFaithfulnessProbe

        payload = run_screen_check(load_dataset(GOLDEN)).to_dict()
        self.assertEqual(payload["probe_id"], CitationFaithfulnessProbe.probe_id)
        self.assertEqual(payload["probe_id"], "citation-faithfulness")


class TestLivePath(unittest.TestCase):
    def test_runs_citation_probe_against_dataset_questions(self):
        ds = GoldenDataset(
            id="live-tiny",
            sources=SOURCES,
            items=(
                GoldenItem(
                    "q1",
                    "What tiers exist?",
                    "unused in live mode",
                    EXPECT_FAITHFUL,
                ),
            ),
        )
        adapter = MockAdapter(
            [
                MockRule.make(
                    responses=(
                        "Northwind Logistics offers three service tiers: "
                        "Standard, Priority, and Overnight."
                    ),
                    mode=MATCH_ANY,
                )
            ]
        )
        evidence = run_live(ds, adapter, min_sample=1, max_unsupported_answer_rate=1.0)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].probe_id, "citation-faithfulness")
        self.assertEqual(evidence[0].config["unit"], "live-tiny")
        self.assertEqual(len(evidence[0].trials), 1)
        self.assertTrue(evidence[0].trials[0].passed)


if __name__ == "__main__":
    unittest.main()
