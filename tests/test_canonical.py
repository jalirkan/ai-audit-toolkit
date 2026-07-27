"""Tests for core.canonical.

The journal's tamper-evidence rests entirely on "same value, same bytes", so
these tests are load-bearing for Phase 2 even though the module is tiny.
"""

from __future__ import annotations

import unittest

from core.canonical import (
    HASH_PREFIX,
    canonical_bytes,
    canonical_json,
    content_hash,
    is_json_serializable,
)


class TestCanonicalJson(unittest.TestCase):
    def test_key_order_does_not_affect_output(self):
        a = {"b": 1, "a": 2, "c": {"z": 1, "y": 2}}
        b = {"c": {"y": 2, "z": 1}, "a": 2, "b": 1}
        self.assertEqual(canonical_json(a), canonical_json(b))

    def test_output_has_no_incidental_whitespace(self):
        self.assertEqual(canonical_json({"a": 1, "b": [1, 2]}), '{"a":1,"b":[1,2]}')

    def test_non_ascii_text_is_preserved_readably(self):
        self.assertEqual(canonical_json({"k": "café"}), '{"k":"café"}')

    def test_encoding_is_utf8(self):
        self.assertEqual(canonical_bytes({"k": "café"}), '{"k":"café"}'.encode("utf-8"))

    def test_rejects_nan_and_infinity(self):
        # These serialize under json's defaults but are not valid JSON and do
        # not survive a round-trip, so they must never reach a stored record.
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    canonical_json({"v": bad})

    def test_rejects_unserializable_objects(self):
        with self.assertRaises(TypeError):
            canonical_json({"v": object()})


class TestContentHash(unittest.TestCase):
    def test_hash_is_algorithm_prefixed(self):
        digest = content_hash({"a": 1})
        self.assertTrue(digest.startswith(HASH_PREFIX))
        self.assertEqual(len(digest.split(":", 1)[1]), 64)

    def test_equal_values_hash_equal_regardless_of_key_order(self):
        self.assertEqual(content_hash({"a": 1, "b": 2}), content_hash({"b": 2, "a": 1}))

    def test_any_change_changes_the_hash(self):
        base = content_hash({"a": 1, "b": "x"})
        self.assertNotEqual(base, content_hash({"a": 2, "b": "x"}))
        self.assertNotEqual(base, content_hash({"a": 1, "b": "y"}))
        self.assertNotEqual(base, content_hash({"a": 1, "b": "x", "c": None}))

    def test_hash_is_stable_across_calls(self):
        payload = {"nested": [1, {"k": "v"}], "t": "2026-07-27T00:00:00Z"}
        self.assertEqual(content_hash(payload), content_hash(payload))

    def test_known_vector(self):
        # Pins the exact encoding: if canonicalization ever changes, this fails
        # rather than silently invalidating previously written journals.
        self.assertEqual(
            content_hash({"a": 1}),
            "sha256:" + __import__("hashlib").sha256(b'{"a":1}').hexdigest(),
        )


class TestIsJsonSerializable(unittest.TestCase):
    def test_accepts_json_native_values(self):
        self.assertTrue(is_json_serializable({"a": [1, 2.5, "x", None, True]}))

    def test_rejects_objects_and_nan(self):
        self.assertFalse(is_json_serializable({"a": object()}))
        self.assertFalse(is_json_serializable({"a": float("nan")}))


if __name__ == "__main__":
    unittest.main()
