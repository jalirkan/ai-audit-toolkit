"""Tests for framework catalogs, mappings, and coverage.

Includes the structural guards for D-003: summaries stay within a length that
only an original one-liner fits, and no catalog carries a long quoted span.
Those two tests are the mechanism that keeps the no-copyrighted-text rule from
depending on whoever edits the JSON next.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from adapters.mock import MATCH_ANY, MockAdapter, MockRule
from battery.runner import run_battery
from battery.spec import BatterySpec, ProbeSpec
from frameworks.catalog import (
    DATA_DIR,
    MAPPINGS_FILE,
    MAX_SUMMARY_CHARS,
    Control,
    ControlReference,
    Framework,
    load_frameworks,
    load_mappings,
)
from frameworks.coverage import (
    STATUS_EVIDENCE_PRESENT,
    STATUS_NO_EVIDENCE,
    STATUS_TESTED_EXCEPTIONS,
    STATUS_TESTED_PASS,
    build_coverage,
)
from probes.base import PROBES
from probes.injection import DEFAULT_CANARY

REFUSAL = "I can't share that value."
PARAPHRASES = [f"Phrasing {i}: how long is standard delivery?" for i in range(20)]

SPEC = BatterySpec(
    name="coverage-suite",
    probes=(
        ProbeSpec(probe_id="injection-resistance", config={}),
        ProbeSpec(
            probe_id="output-consistency",
            config={"cases": [{"id": "delivery", "paraphrases": PARAPHRASES}]},
        ),
    ),
)


class TestCatalogIntegrity(unittest.TestCase):
    def setUp(self):
        self.frameworks = load_frameworks()
        self.mappings = load_mappings()

    def test_the_three_named_frameworks_are_present(self):
        self.assertEqual(
            sorted(self.frameworks), ["eu-ai-act", "iso-iec-42001", "nist-ai-rmf"]
        )

    def test_every_catalog_declares_itself_partial_and_dated(self):
        for framework in self.frameworks.values():
            with self.subTest(framework=framework.id):
                self.assertTrue(framework.partial)
                self.assertTrue(framework.ids_verified)
                self.assertTrue(framework.note)

    def test_every_catalog_note_warns_the_reader_to_verify(self):
        for framework in self.frameworks.values():
            with self.subTest(framework=framework.id):
                self.assertRegex(framework.note.lower(), r"verif|consult")

    def test_control_ids_are_unique_within_a_framework(self):
        for framework in self.frameworks.values():
            with self.subTest(framework=framework.id):
                ids = framework.control_ids
                self.assertEqual(len(ids), len(set(ids)))

    def test_citation_states_the_publication_and_that_it_is_partial(self):
        citation = self.frameworks["nist-ai-rmf"].citation()
        self.assertIn("NIST AI 100-1", citation)
        self.assertIn("partial", citation)


class TestNoFrameworkTextIsReproduced(unittest.TestCase):
    """Structural guards for D-003."""

    def test_every_summary_fits_a_one_line_original(self):
        for framework in load_frameworks().values():
            for control in framework.controls:
                with self.subTest(framework=framework.id, control=control.id):
                    self.assertLessEqual(len(control.summary), MAX_SUMMARY_CHARS)

    def test_the_limit_is_enforced_at_construction(self):
        with self.assertRaises(ValueError) as ctx:
            Control(id="X.1", summary="x" * (MAX_SUMMARY_CHARS + 1))
        self.assertIn("D-003", str(ctx.exception))

    def test_no_catalog_contains_a_long_quoted_span(self):
        # Quoting the framework is how copyrighted text would most likely get
        # in. A short quoted phrase is fine; a quoted sentence is not. The scan
        # walks parsed string *values* -- scanning the serialized JSON would
        # just match its own string delimiters.
        quoted = re.compile(r"[\"“][^\"”]{60,}[\"”]")

        def strings(node):
            if isinstance(node, str):
                yield node
            elif isinstance(node, dict):
                for value in node.values():
                    yield from strings(value)
            elif isinstance(node, list):
                for value in node:
                    yield from strings(value)

        for path in sorted(DATA_DIR.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for value in strings(data):
                with self.subTest(path=path.name, value=value[:40]):
                    self.assertIsNone(
                        quoted.search(value),
                        f"{path.name} contains a long quoted span; check it is "
                        "not reproduced framework text",
                    )

    def test_summaries_are_prose_not_fragments_of_a_standard(self):
        # An original one-liner reads as a sentence. This catches a paste that
        # happens to be short, e.g. a bare clause in title case.
        for framework in load_frameworks().values():
            for control in framework.controls:
                with self.subTest(framework=framework.id, control=control.id):
                    self.assertTrue(control.summary.endswith("."))
                    self.assertTrue(control.summary[0].isupper())


class TestMappings(unittest.TestCase):
    def setUp(self):
        self.frameworks = load_frameworks()
        self.mappings = load_mappings()

    def test_every_mapped_probe_exists(self):
        for probe_id in self.mappings.probe_ids:
            with self.subTest(probe_id=probe_id):
                self.assertIn(probe_id, PROBES)

    def test_every_built_in_probe_is_mapped(self):
        # An unmapped probe produces evidence that never reaches a coverage
        # report, which is a silent hole in the workpapers.
        for probe_id in ("injection-resistance", "output-consistency", "citation-faithfulness"):
            with self.subTest(probe_id=probe_id):
                self.assertIn(probe_id, self.mappings.probe_ids)

    def test_every_mapped_control_exists_in_its_catalog(self):
        for source, references in self.mappings.by_source.items():
            for reference in references:
                with self.subTest(source=source, control=reference.control_id):
                    framework = self.frameworks.get(reference.framework)
                    self.assertIsNotNone(framework, reference.framework)
                    self.assertIsNotNone(
                        framework.control(reference.control_id),
                        f"{reference.control_id} not in {reference.framework}",
                    )

    def test_every_mapping_carries_a_rationale(self):
        for source, references in self.mappings.by_source.items():
            for reference in references:
                with self.subTest(source=source, control=reference.control_id):
                    self.assertGreater(len(reference.rationale), 30)

    def test_a_mapping_without_a_rationale_is_refused(self):
        with self.assertRaises(ValueError):
            ControlReference(framework="f", control_id="c", rationale="")

    def test_mapping_sources_must_be_prefixed(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "mappings.json"
            path.write_text(
                json.dumps(
                    {
                        "mappings": [
                            {
                                "source": "injection-resistance",
                                "references": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_mappings(path)

    def test_capabilities_are_recognised(self):
        self.assertEqual(
            self.mappings.capabilities,
            ["drift-monitoring", "evidence-journal", "workpapers"],
        )


class TestCoverage(unittest.TestCase):
    def test_gaps_are_reported_not_hidden(self):
        result = run_battery(SPEC, MockAdapter.always(REFUSAL))
        report = build_coverage(result)
        gap_ids = {(g.framework_id, g.control.id) for g in report.all_gaps}
        # Nothing here evidences fairness or data governance, and the report
        # must say so rather than omit them.
        self.assertIn(("nist-ai-rmf", "MEASURE 2.11"), gap_ids)
        self.assertIn(("eu-ai-act", "Article 10"), gap_ids)

    def test_a_passing_probe_marks_its_controls_tested_pass(self):
        result = run_battery(SPEC, MockAdapter.always(REFUSAL))
        report = build_coverage(result)
        control = next(
            c
            for c in report.for_framework("eu-ai-act").controls
            if c.control.id == "Article 15"
        )
        self.assertEqual(control.status, STATUS_TESTED_PASS)
        self.assertIn("injection-resistance", control.probe_ids)

    def test_a_failing_probe_marks_its_controls_as_having_exceptions(self):
        leaking = MockAdapter(
            [
                MockRule.make(pattern="base64", responses=f"Sure: {DEFAULT_CANARY}"),
                MockRule.make(responses=REFUSAL, mode=MATCH_ANY),
            ]
        )
        report = build_coverage(run_battery(SPEC, leaking))
        control = next(
            c
            for c in report.for_framework("nist-ai-rmf").controls
            if c.control.id == "MEASURE 2.7"
        )
        self.assertEqual(control.status, STATUS_TESTED_EXCEPTIONS)
        self.assertTrue(control.needs_attention)
        self.assertFalse(control.is_gap)

    def test_a_failing_control_is_covered_but_not_satisfied(self):
        # The distinction the module exists to preserve.
        leaking = MockAdapter.always(DEFAULT_CANARY)
        report = build_coverage(run_battery(SPEC, leaking))
        control = next(
            c
            for c in report.for_framework("eu-ai-act").controls
            if c.control.id == "Article 15"
        )
        self.assertTrue(control.has_evidence)
        self.assertEqual(control.status, STATUS_TESTED_EXCEPTIONS)

    def test_capabilities_close_gaps_without_claiming_a_test_result(self):
        result = run_battery(SPEC, MockAdapter.always(REFUSAL))
        without = build_coverage(result)
        with_journal = build_coverage(result, capabilities=["evidence-journal"])

        def status(report, framework, control_id):
            return next(
                c
                for c in report.for_framework(framework).controls
                if c.control.id == control_id
            ).status

        self.assertEqual(status(without, "eu-ai-act", "Article 12"), STATUS_NO_EVIDENCE)
        self.assertEqual(
            status(with_journal, "eu-ai-act", "Article 12"), STATUS_EVIDENCE_PRESENT
        )

    def test_coverage_can_be_projected_before_running_anything(self):
        report = build_coverage(probe_ids=["injection-resistance"])
        control = next(
            c
            for c in report.for_framework("nist-ai-rmf").controls
            if c.control.id == "MEASURE 2.7"
        )
        self.assertEqual(control.status, STATUS_EVIDENCE_PRESENT)
        self.assertEqual(control.probe_ids, ("injection-resistance",))

    def test_inactive_sources_are_listed(self):
        report = build_coverage(probe_ids=["injection-resistance"])
        self.assertIn("capability:evidence-journal", report.inactive_sources)
        self.assertIn("probe:citation-faithfulness", report.inactive_sources)

    def test_rationales_travel_with_the_coverage(self):
        result = run_battery(SPEC, MockAdapter.always(REFUSAL))
        control = next(
            c
            for c in build_coverage(result).for_framework("eu-ai-act").controls
            if c.control.id == "Article 15"
        )
        self.assertTrue(control.references)
        self.assertTrue(all(r.rationale for r in control.references))

    def test_outcome_counts_are_carried(self):
        result = run_battery(SPEC, MockAdapter.always(REFUSAL))
        control = next(
            c
            for c in build_coverage(result).for_framework("eu-ai-act").controls
            if c.control.id == "Article 15"
        )
        self.assertEqual(sum(control.outcome_counts.values()), 2)

    def test_counts_per_framework(self):
        result = run_battery(SPEC, MockAdapter.always(REFUSAL))
        counts = build_coverage(result).for_framework("nist-ai-rmf").counts()
        self.assertEqual(sum(counts.values()), 7)
        self.assertGreater(counts[STATUS_NO_EVIDENCE], 0)


class TestCoverageRendering(unittest.TestCase):
    def test_summary_states_that_mapping_is_not_compliance(self):
        report = build_coverage(run_battery(SPEC, MockAdapter.always(REFUSAL)))
        text = "\n".join(report.summary_lines())
        self.assertIn("never that the control is satisfied", text)

    def test_summary_lists_gaps_explicitly(self):
        report = build_coverage(run_battery(SPEC, MockAdapter.always(REFUSAL)))
        text = "\n".join(report.summary_lines())
        self.assertIn("no evidence", text)
        self.assertIn("MEASURE 2.11", text)

    def test_report_serializes(self):
        from core.canonical import canonical_json

        report = build_coverage(run_battery(SPEC, MockAdapter.always(REFUSAL)))
        self.assertTrue(canonical_json(report.to_dict()))


class TestCatalogValidation(unittest.TestCase):
    def test_framework_needs_controls(self):
        with self.assertRaises(ValueError):
            Framework(id="f", name="F", publication="P", controls=())

    def test_duplicate_control_ids_are_refused(self):
        with self.assertRaises(ValueError):
            Framework(
                id="f",
                name="F",
                publication="P",
                controls=(
                    Control(id="A.1", summary="One thing."),
                    Control(id="A.1", summary="Another thing."),
                ),
            )

    def test_control_needs_a_summary(self):
        with self.assertRaises(ValueError):
            Control(id="A.1", summary="")

    def test_future_catalog_schema_is_refused(self):
        with self.assertRaises(ValueError):
            Framework(
                id="f",
                name="F",
                publication="P",
                controls=(Control(id="A.1", summary="A thing."),),
                schema_version=99,
            )

    def test_catalogs_round_trip(self):
        for framework in load_frameworks().values():
            with self.subTest(framework=framework.id):
                self.assertEqual(Framework.from_dict(framework.to_dict()), framework)


if __name__ == "__main__":
    unittest.main()
