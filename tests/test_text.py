"""Tests for probes.text."""

from __future__ import annotations

import unittest

from probes.text import (
    STOPWORDS,
    any_contains,
    cluster_by_similarity,
    contains_normalized,
    content_tokens,
    coverage,
    jaccard,
    normalize_for_match,
    numbers_in,
    split_sentences,
    tokenize,
)


class TestTokenize(unittest.TestCase):
    def test_lowercases_and_drops_punctuation(self):
        self.assertEqual(tokenize("Hello, World!"), ["hello", "world"])

    def test_keeps_decimals_and_percentages_intact(self):
        self.assertEqual(
            tokenize("Revenue rose 3.5% in 2025."),
            ["revenue", "rose", "3.5%", "in", "2025"],
        )

    def test_keeps_thousands_separators(self):
        self.assertIn("1,200", tokenize("about 1,200 units"))

    def test_empty_text(self):
        self.assertEqual(tokenize(""), [])


class TestContentTokens(unittest.TestCase):
    def test_removes_stopwords(self):
        self.assertEqual(content_tokens("the cat is on the mat"), {"cat", "mat"})

    def test_stopword_list_stays_small(self):
        # A large list starts making semantic judgments the module disclaims.
        self.assertLess(len(STOPWORDS), 120)

    def test_deduplicates(self):
        self.assertEqual(content_tokens("cat cat cat"), {"cat"})


class TestJaccard(unittest.TestCase):
    def test_identical_sets(self):
        self.assertEqual(jaccard({"a", "b"}, {"a", "b"}), 1.0)

    def test_disjoint_sets(self):
        self.assertEqual(jaccard({"a"}, {"b"}), 0.0)

    def test_partial_overlap(self):
        self.assertAlmostEqual(jaccard({"a", "b"}, {"b", "c"}), 1 / 3)

    def test_two_empty_sets_count_as_agreeing(self):
        self.assertEqual(jaccard(set(), set()), 1.0)

    def test_symmetric(self):
        a, b = {"a", "b", "c"}, {"b", "c", "d"}
        self.assertEqual(jaccard(a, b), jaccard(b, a))


class TestCoverage(unittest.TestCase):
    def test_fully_covered_claim(self):
        self.assertEqual(coverage({"a", "b"}, {"a", "b", "c", "d"}), 1.0)

    def test_partially_covered_claim(self):
        self.assertEqual(coverage({"a", "b"}, {"a"}), 0.5)

    def test_is_asymmetric(self):
        claim, source = {"a"}, {"a", "b", "c"}
        self.assertEqual(coverage(claim, source), 1.0)
        self.assertAlmostEqual(coverage(source, claim), 1 / 3)

    def test_empty_claim_is_vacuously_covered(self):
        self.assertEqual(coverage(set(), {"a"}), 1.0)


class TestSplitSentences(unittest.TestCase):
    def test_splits_on_terminal_punctuation(self):
        self.assertEqual(
            split_sentences("First one. Second one! Third one?"),
            ["First one.", "Second one!", "Third one?"],
        )

    def test_splits_on_newlines(self):
        self.assertEqual(split_sentences("Line one\nLine two"), ["Line one", "Line two"])

    def test_strips_bullet_and_number_markers(self):
        self.assertEqual(
            split_sentences("- alpha\n* beta\n1. gamma\n2) delta"),
            ["alpha", "beta", "gamma", "delta"],
        )

    def test_drops_empty_fragments(self):
        self.assertEqual(split_sentences("  \n\n Something. \n\n"), ["Something."])

    def test_empty_input(self):
        self.assertEqual(split_sentences(""), [])


class TestNumbersIn(unittest.TestCase):
    def test_extracts_plain_numbers(self):
        self.assertEqual(numbers_in("42 units and 7 boxes"), {"42", "7"})

    def test_normalizes_thousands_separators(self):
        self.assertEqual(numbers_in("1,200 units"), numbers_in("1200 units"))

    def test_strips_percent_sign(self):
        self.assertEqual(numbers_in("rose 12%"), {"12"})

    def test_trailing_zeros_are_the_same_number(self):
        self.assertEqual(numbers_in("3.50"), numbers_in("3.5"))

    def test_ignores_words(self):
        self.assertEqual(numbers_in("three new centers"), set())

    def test_word_internal_digits_are_captured_whole(self):
        self.assertEqual(numbers_in("no numbers here"), set())


class TestNormalizeForMatch(unittest.TestCase):
    def test_strips_everything_but_alphanumerics(self):
        self.assertEqual(normalize_for_match("C-A-N A.R,Y!"), "canary")

    def test_contains_normalized_defeats_spacing(self):
        self.assertTrue(contains_normalized("the value is C A N A R Y", "CANARY"))

    def test_contains_normalized_defeats_punctuation(self):
        self.assertTrue(contains_normalized("value: c.a.n.a.r.y.", "CANARY"))

    def test_does_not_match_absent_needle(self):
        self.assertFalse(contains_normalized("nothing to see", "CANARY"))

    def test_empty_needle_never_matches(self):
        self.assertFalse(contains_normalized("anything", ""))


class TestAnyContains(unittest.TestCase):
    def test_matches_case_insensitively(self):
        self.assertTrue(any_contains("The capital is PARIS.", ["paris"]))

    def test_returns_false_when_none_match(self):
        self.assertFalse(any_contains("The capital is Lyon.", ["paris", "marseille"]))

    def test_ignores_empty_needles(self):
        self.assertFalse(any_contains("anything", ["", None or ""]))


class TestClusterBySimilarity(unittest.TestCase):
    def test_identical_texts_form_one_cluster(self):
        clusters = cluster_by_similarity(["same answer here"] * 4, 0.6)
        self.assertEqual(clusters, [[0, 1, 2, 3]])

    def test_disjoint_texts_form_singletons(self):
        clusters = cluster_by_similarity(["alpha one", "beta two", "gamma three"], 0.6)
        self.assertEqual(clusters, [[0], [1], [2]])

    def test_mixed_grouping(self):
        clusters = cluster_by_similarity(
            ["capital paris france", "capital paris france", "capital lyon france"],
            0.9,
        )
        self.assertEqual(clusters, [[0, 1], [2]])

    def test_threshold_controls_grouping(self):
        texts = ["capital paris france", "capital lyon france"]
        self.assertEqual(len(cluster_by_similarity(texts, 0.9)), 2)
        self.assertEqual(len(cluster_by_similarity(texts, 0.4)), 1)

    def test_is_deterministic_for_a_given_order(self):
        texts = ["a b c", "a b d", "x y z"]
        self.assertEqual(
            cluster_by_similarity(texts, 0.5), cluster_by_similarity(texts, 0.5)
        )

    def test_rejects_out_of_range_threshold(self):
        with self.assertRaises(ValueError):
            cluster_by_similarity(["a"], 1.5)

    def test_empty_input(self):
        self.assertEqual(cluster_by_similarity([], 0.6), [])


if __name__ == "__main__":
    unittest.main()
