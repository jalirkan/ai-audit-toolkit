"""Tests for the hash-chained evidence journal.

The tamper cases are the point of the module, so they are tested by actually
tampering: the triggers are dropped and the rows rewritten with raw SQL, which
is what someone with file access would do. A chain that only detects tampering
the toolkit itself performs would prove nothing.
"""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.canonical import canonical_json, text_hash
from core.evidence import (
    OUTCOME_PASS,
    Evidence,
    Measurement,
    ModelFingerprint,
    Trial,
)
from journal.store import (
    GENESIS_HASH,
    KIND_EVIDENCE,
    KIND_NOTE,
    KIND_RUN,
    Journal,
)


def make_evidence(probe_id: str = "injection-resistance") -> Evidence:
    return Evidence(
        probe_id=probe_id,
        outcome=OUTCOME_PASS,
        fingerprint=ModelFingerprint(adapter="mock", model="mock-deterministic-v1"),
        started_at="2026-07-27T00:00:00.000000Z",
        finished_at="2026-07-27T00:00:01.000000Z",
        trials=(Trial(index=0, prompt="p", response_text="r", passed=True),),
        measurements=(Measurement.proportion("leak_rate", 0, 22),),
        config={"unit": "baseline"},
    )


def unlocked(path: str) -> sqlite3.Connection:
    """Open the database with the append-only triggers removed.

    Simulates an attacker with write access to the file. Everything after this
    point is the chain's job.
    """
    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER IF EXISTS journal_no_update")
    conn.execute("DROP TRIGGER IF EXISTS journal_no_delete")
    conn.commit()
    return conn


class TestAppendAndRead(unittest.TestCase):
    def setUp(self):
        self.journal = Journal()
        self.addCleanup(self.journal.close)

    def test_empty_journal_verifies(self):
        result = self.journal.verify()
        self.assertTrue(result.ok)
        self.assertEqual(result.entries_checked, 0)
        self.assertEqual(result.head_hash, GENESIS_HASH)

    def test_first_entry_chains_from_genesis(self):
        entry = self.journal.append_note("first")
        self.assertEqual(entry.seq, 1)
        self.assertEqual(entry.prev_hash, GENESIS_HASH)

    def test_each_entry_chains_from_the_last(self):
        a = self.journal.append_note("a")
        b = self.journal.append_note("b")
        c = self.journal.append_note("c")
        self.assertEqual(b.prev_hash, a.entry_hash)
        self.assertEqual(c.prev_hash, b.entry_hash)
        self.assertEqual([a.seq, b.seq, c.seq], [1, 2, 3])

    def test_head_tracks_the_latest_entry(self):
        self.journal.append_note("a")
        last = self.journal.append_note("b")
        self.assertEqual(self.journal.head(), last.entry_hash)

    def test_evidence_round_trips_through_storage(self):
        original = make_evidence()
        self.journal.append_evidence(original)
        [restored] = self.journal.evidence()
        self.assertEqual(restored, original)
        self.assertEqual(restored.content_hash(), original.content_hash())

    def test_entries_are_returned_in_order(self):
        for i in range(5):
            self.journal.append_note(f"note {i}")
        self.assertEqual([e.seq for e in self.journal.entries()], [1, 2, 3, 4, 5])

    def test_entries_can_be_filtered_by_kind(self):
        self.journal.append_note("n")
        self.journal.append_evidence(make_evidence())
        self.journal.append_run({"battery": "b", "run_id": "r"})
        self.assertEqual(len(self.journal.entries_of_kind(KIND_EVIDENCE)), 1)
        self.assertEqual(len(self.journal.entries_of_kind(KIND_RUN)), 1)
        self.assertEqual(len(self.journal.entries_of_kind(KIND_NOTE)), 1)

    def test_len_counts_entries(self):
        self.journal.append_note("a")
        self.journal.append_note("b")
        self.assertEqual(len(self.journal), 2)

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self.journal.append("speculation", {"x": 1})

    def test_unserializable_payload_is_rejected(self):
        with self.assertRaises(TypeError):
            self.journal.append(KIND_NOTE, {"fn": object()})

    def test_as_evidence_refuses_a_non_evidence_entry(self):
        entry = self.journal.append_note("n")
        with self.assertRaises(ValueError):
            entry.as_evidence()

    def test_many_entries_still_verify(self):
        for i in range(200):
            self.journal.append_note(f"note {i}")
        result = self.journal.verify()
        self.assertTrue(result.ok)
        self.assertEqual(result.entries_checked, 200)


class TestAppendOnlyTriggers(unittest.TestCase):
    """The first line of defence: history cannot be edited through SQL at all."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "journal.db")
        self.journal = Journal(self.path)
        self.journal.append_note("original")
        self.journal.close()

    def test_update_is_refused(self):
        conn = sqlite3.connect(self.path)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE journal SET payload = '{\"text\":\"edited\"}'")
        conn.close()

    def test_delete_is_refused(self):
        conn = sqlite3.connect(self.path)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM journal WHERE seq = 1")
        conn.close()


class TestTamperDetection(unittest.TestCase):
    """The second line of defence, tested with the triggers removed."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "journal.db")
        journal = Journal(self.path)
        for i in range(5):
            journal.append_note(f"entry {i}")
        self.clean_head = journal.head()
        journal.close()

    def reopen(self) -> Journal:
        journal = Journal(self.path)
        self.addCleanup(journal.close)
        return journal

    def test_untouched_journal_verifies(self):
        result = self.reopen().verify()
        self.assertTrue(result.ok, result.summary())
        self.assertEqual(result.entries_checked, 5)

    def test_edited_payload_is_detected(self):
        conn = unlocked(self.path)
        conn.execute(
            "UPDATE journal SET payload = ? WHERE seq = 3",
            (canonical_json({"text": "quietly changed"}),),
        )
        conn.commit()
        conn.close()

        result = self.reopen().verify()
        self.assertFalse(result.ok)
        codes = {p.code for p in result.problems}
        self.assertIn("payload-modified", codes)
        self.assertEqual(result.problems[0].seq, 3)

    def test_payload_edited_with_a_matching_hash_is_still_detected(self):
        # A tamperer who recomputes the payload hash still has to contend with
        # the entry hash, which binds the payload hash to the chain.
        new_payload = canonical_json({"text": "quietly changed"})
        conn = unlocked(self.path)
        conn.execute(
            "UPDATE journal SET payload = ?, payload_hash = ? WHERE seq = 3",
            (new_payload, text_hash(new_payload)),
        )
        conn.commit()
        conn.close()

        result = self.reopen().verify()
        self.assertFalse(result.ok)
        self.assertIn("entry-hash-mismatch", {p.code for p in result.problems})

    def test_recomputing_one_entry_hash_breaks_the_next_link(self):
        # Even recomputing the entry hash is not enough: every later entry
        # asserts the old value as its predecessor.
        journal = Journal(self.path)
        entries = journal.entries()
        journal.close()

        new_payload = canonical_json({"text": "quietly changed"})
        new_payload_hash = text_hash(new_payload)
        from journal.store import _compute_entry_hash

        target = entries[2]
        forged_hash = _compute_entry_hash(
            target.seq,
            target.recorded_at,
            target.kind,
            new_payload_hash,
            target.prev_hash,
        )
        conn = unlocked(self.path)
        conn.execute(
            "UPDATE journal SET payload = ?, payload_hash = ?, entry_hash = ? "
            "WHERE seq = 3",
            (new_payload, new_payload_hash, forged_hash),
        )
        conn.commit()
        conn.close()

        result = self.reopen().verify()
        self.assertFalse(result.ok)
        broken = [p for p in result.problems if p.code == "broken-link"]
        self.assertEqual([p.seq for p in broken], [4])

    def test_deleted_entry_is_detected(self):
        conn = unlocked(self.path)
        conn.execute("DELETE FROM journal WHERE seq = 3")
        conn.commit()
        conn.close()

        result = self.reopen().verify()
        self.assertFalse(result.ok)
        codes = {p.code for p in result.problems}
        self.assertIn("sequence-gap", codes)
        self.assertIn("broken-link", codes)

    def test_truncating_the_tail_is_detected_only_against_an_anchor(self):
        # Removing the most recent entries leaves a chain that is internally
        # consistent. This is exactly the case an external anchor exists for.
        conn = unlocked(self.path)
        conn.execute("DELETE FROM journal WHERE seq >= 4")
        conn.commit()
        conn.close()

        journal = self.reopen()
        self.assertTrue(journal.verify().ok)

        anchored = journal.verify(expected_head=self.clean_head)
        self.assertFalse(anchored.ok)
        self.assertIn("head-mismatch", {p.code for p in anchored.problems})

    def test_reordered_entries_are_detected(self):
        conn = unlocked(self.path)
        rows = conn.execute(
            "SELECT payload, payload_hash FROM journal WHERE seq IN (2, 3) ORDER BY seq"
        ).fetchall()
        conn.execute(
            "UPDATE journal SET payload = ?, payload_hash = ? WHERE seq = 2",
            (rows[1][0], rows[1][1]),
        )
        conn.execute(
            "UPDATE journal SET payload = ?, payload_hash = ? WHERE seq = 3",
            (rows[0][0], rows[0][1]),
        )
        conn.commit()
        conn.close()

        result = self.reopen().verify()
        self.assertFalse(result.ok)
        self.assertEqual({p.seq for p in result.problems if p.code == "entry-hash-mismatch"}, {2, 3})

    def test_appended_forgery_is_detected(self):
        conn = unlocked(self.path)
        payload = canonical_json({"text": "inserted after the fact"})
        conn.execute(
            "INSERT INTO journal "
            "(seq, recorded_at, kind, payload, payload_hash, prev_hash, entry_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                6,
                "2026-07-27T09:00:00.000000Z",
                KIND_NOTE,
                payload,
                text_hash(payload),
                GENESIS_HASH,
                "sha256:" + "f" * 64,
            ),
        )
        conn.commit()
        conn.close()

        result = self.reopen().verify()
        self.assertFalse(result.ok)
        codes = {p.code for p in result.problems}
        self.assertIn("broken-link", codes)
        self.assertIn("entry-hash-mismatch", codes)

    def test_a_full_rebuild_verifies_which_is_why_anchoring_matters(self):
        # Documented limitation, asserted so nobody assumes otherwise: someone
        # who can rewrite the whole file can produce a valid chain. Only the
        # anchored head catches it.
        conn = unlocked(self.path)
        conn.execute("DELETE FROM journal")
        conn.commit()
        conn.close()

        rebuilt = self.reopen()
        for i in range(5):
            rebuilt.append_note(f"fabricated {i}")

        self.assertTrue(rebuilt.verify().ok)
        self.assertFalse(rebuilt.verify(expected_head=self.clean_head).ok)

    def test_all_problems_are_reported_not_just_the_first(self):
        conn = unlocked(self.path)
        for seq in (2, 4):
            conn.execute(
                "UPDATE journal SET payload = ? WHERE seq = ?",
                (canonical_json({"text": f"edited {seq}"}), seq),
            )
        conn.commit()
        conn.close()

        result = self.reopen().verify()
        modified = [p for p in result.problems if p.code == "payload-modified"]
        self.assertEqual([p.seq for p in modified], [2, 4])


class TestPersistence(unittest.TestCase):
    def test_journal_survives_reopening(self):
        with TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "nested" / "journal.db")
            first = Journal(path)
            first.append_evidence(make_evidence())
            head = first.head()
            first.close()

            second = Journal(path)
            self.addCleanup(second.close)
            self.assertEqual(len(second), 1)
            self.assertEqual(second.head(), head)
            self.assertTrue(second.verify(expected_head=head).ok)

    def test_parent_directories_are_created(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "a" / "b" / "journal.db"
            journal = Journal(path)
            self.addCleanup(journal.close)
            self.assertTrue(path.exists())

    def test_context_manager_closes(self):
        with TemporaryDirectory() as tmp:
            with Journal(Path(tmp) / "j.db") as journal:
                journal.append_note("x")
            with self.assertRaises(sqlite3.ProgrammingError):
                journal.head()


class TestVerificationResultRendering(unittest.TestCase):
    def test_clean_summary_names_the_head(self):
        journal = Journal()
        self.addCleanup(journal.close)
        journal.append_note("x")
        summary = journal.verify().summary()
        self.assertIn("Chain intact", summary)
        self.assertIn(journal.head()[7:19], summary)

    def test_broken_summary_leads_with_the_first_problem(self):
        with TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "j.db")
            journal = Journal(path)
            journal.append_note("a")
            journal.append_note("b")
            journal.close()

            conn = unlocked(path)
            conn.execute(
                "UPDATE journal SET payload = ? WHERE seq = 1",
                (canonical_json({"text": "edited"}),),
            )
            conn.commit()
            conn.close()

            journal = Journal(path)
            self.addCleanup(journal.close)
            summary = journal.verify().summary()
            self.assertIn("BROKEN", summary)
            self.assertIn("entry 1", summary)


if __name__ == "__main__":
    unittest.main()
