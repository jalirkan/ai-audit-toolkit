"""Tests for the read-only evidence API.

Three groups here carry weight beyond ordinary route coverage:

**Traversal is unrepresentable, and the test says so by trying.** Every
parameter that could reach the filesystem is attacked with the usual shapes --
``../``, absolute paths, encoded separators, null bytes -- and must be refused
without touching disk.

**No credential reaches a response.** The adapters already take care not to
record one; this asserts the server does not undo that. As with the no-bare-
rates scan in ``test_report.py``, there is a companion test that plants a
secret and proves the scan actually fails on it -- a guard that cannot fail
proves nothing.

**The server is stdlib-only, checked by parsing its imports** rather than by
trusting a reviewer to notice. D-001 is the constraint the whole project rests
on; a front end that quietly added a dependency to the Python side would take
the engine's zero-install guarantee with it.
"""

from __future__ import annotations

import ast
import json
import shutil
import sys
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote

from adapters.mock import MockAdapter, load_mock_script
from battery.runner import run_battery
from battery.spec import BatterySpec
from drift.baseline import BaselineStore
from journal.store import Journal
from serve import (
    API_VERSION,
    CHAIN_LIMITS,
    HOST,
    ApiError,
    AuditApi,
    make_handler,
    serve,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

SCRIPTED_STARTED_AT = "2026-07-31T09:00:00.000000Z"
BARE_STARTED_AT = "2026-07-31T09:30:00.000000Z"
BASELINE_LABEL = "q3-2026"


def build_workspace(workspace: Path) -> AuditApi:
    """Two real runs, a journal, and a baseline in a scratch directory."""
    shutil.copytree(REPO_ROOT / "suites", workspace / "suites")
    shutil.copytree(REPO_ROOT / "datasets", workspace / "datasets")
    shutil.copy(REPO_ROOT / "DECISIONS.md", workspace / "DECISIONS.md")

    spec = BatterySpec.load(workspace / "suites" / "baseline.json")
    runs_dir = workspace / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    scripted = load_mock_script(workspace / "suites" / "demo-endpoint.json")
    bare = MockAdapter(model="bare-mock")

    with Journal(workspace / "runs" / "journal.db") as jrnl:
        results = [
            run_battery(spec, scripted, journal=jrnl, started_at=SCRIPTED_STARTED_AT),
            run_battery(spec, bare, journal=jrnl, started_at=BARE_STARTED_AT),
        ]
    for result in results:
        (runs_dir / f"{result.run_id}.json").write_text(
            json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8"
        )
    BaselineStore(workspace / "baselines").save(
        BASELINE_LABEL, results[0], note="reference"
    )
    return AuditApi(root=workspace)


class ApiTestCase(unittest.TestCase):
    """Base fixture: a real workspace and an API over it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = TemporaryDirectory()
        cls.workspace = Path(cls._tmp.name)
        cls.api = build_workspace(cls.workspace)
        runs = cls.api.runs()
        cls.by_model = {r["fingerprint"]["model"]: r["run_id"] for r in runs}
        cls.scripted_id = cls.by_model["demo-vendor-assistant"]
        cls.bare_id = cls.by_model["bare-mock"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()


# --- payload shapes ----------------------------------------------------------


class TestRunsIndex(ApiTestCase):
    def test_lists_every_stored_run(self):
        self.assertEqual(len(self.api.runs()), 2)

    def test_index_carries_outcome_counts_not_a_score(self):
        entry = self.api.runs()[0]
        self.assertIn("outcome_counts", entry)
        for forbidden in ("score", "composite", "average", "grade", "health"):
            self.assertNotIn(forbidden, entry)

    def test_run_detail_is_the_engine_payload_verbatim(self):
        stored = json.loads(
            (self.workspace / "runs" / f"{self.scripted_id}.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(self.api.run(self.scripted_id), stored)

    def test_the_demo_run_still_shows_all_three_outcomes(self):
        # D-034: a sample run that is all green teaches nothing. If this breaks,
        # the fixture has drifted and every UI state test loses its subject.
        counts = self.api.run(self.scripted_id)["outcome_counts"]
        self.assertEqual(counts["fail"], 1)
        self.assertEqual(counts["pass"], 1)
        self.assertEqual(counts["inconclusive"], 1)

    def test_unknown_run_names_the_ones_that_exist(self):
        with self.assertRaises(ApiError) as ctx:
            self.api.run("0" * 16)
        self.assertEqual(ctx.exception.status, 404)
        self.assertIn(self.scripted_id, ctx.exception.message)


class TestCoverage(ApiTestCase):
    def test_gaps_are_present_not_filtered_out(self):
        payload = self.api.coverage(self.scripted_id, ())
        statuses = {
            c["status"]
            for f in payload["frameworks"]
            for c in f["controls"]
        }
        self.assertIn("no-evidence", statuses)

    def test_the_mapping_disclaimer_travels_with_the_payload(self):
        payload = self.api.coverage(self.scripted_id, ())
        self.assertIn("never that the control is satisfied", payload["disclaimer"])

    def test_declared_capabilities_reach_the_report(self):
        payload = self.api.coverage(self.scripted_id, ("evidence-journal",))
        self.assertEqual(payload["active_capabilities"], ["evidence-journal"])

    def test_a_malformed_capability_is_refused(self):
        with self.assertRaises(ApiError) as ctx:
            self.api.coverage(self.scripted_id, ("Evidence Journal",))
        self.assertEqual(ctx.exception.status, 400)


class TestJournal(ApiTestCase):
    def test_entries_carry_the_chain_fields(self):
        payload = self.api.journal_entries()
        entry = payload["entries"][0]
        for key in ("seq", "kind", "payload_hash", "prev_hash", "entry_hash"):
            self.assertIn(key, entry)
        self.assertEqual(payload["head"], payload["entries"][-1]["entry_hash"])

    def test_payloads_are_omitted_unless_requested(self):
        self.assertNotIn("payload", self.api.journal_entries()["entries"][0])
        with_payload = self.api.journal_entries(include_payload=True)
        self.assertIn("payload", with_payload["entries"][0])

    def test_paging_windows_the_chain(self):
        first = self.api.journal_entries(limit=2, offset=0)
        second = self.api.journal_entries(limit=2, offset=2)
        self.assertEqual(len(first["entries"]), 2)
        self.assertEqual(first["total"], second["total"])
        self.assertNotEqual(
            [e["seq"] for e in first["entries"]],
            [e["seq"] for e in second["entries"]],
        )

    def test_an_absurd_limit_is_refused(self):
        with self.assertRaises(ApiError):
            self.api.journal_entries(limit=10_000)

    def test_verification_states_what_it_does_not_prove(self):
        payload = self.api.journal_verify()
        self.assertTrue(payload["ok"])
        # D-017: a green result without this sentence is an overclaim.
        self.assertEqual(payload["does_not_prove"], CHAIN_LIMITS)
        self.assertIn("full rebuild", payload["does_not_prove"])

    def test_an_anchored_head_that_matches_verifies(self):
        head = self.api.journal_entries(limit=1)["head"]
        payload = self.api.journal_verify(head)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["anchored"])

    def test_a_wrong_anchor_is_reported_as_a_problem(self):
        payload = self.api.journal_verify("sha256:" + "b" * 64)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["problems"][0]["code"], "head-mismatch")

    def test_a_malformed_anchor_is_refused_before_verifying(self):
        with self.assertRaises(ApiError) as ctx:
            self.api.journal_verify("not-a-hash")
        self.assertEqual(ctx.exception.status, 400)


class TestDrift(ApiTestCase):
    def test_baselines_are_listed_with_their_run(self):
        entries = self.api.baselines()
        self.assertEqual(entries[0]["label"], BASELINE_LABEL)
        self.assertEqual(entries[0]["run_id"], self.scripted_id)

    def test_a_changed_endpoint_reports_drift(self):
        payload = self.api.drift(BASELINE_LABEL, self.bare_id)
        self.assertTrue(payload["has_drift"])
        self.assertTrue(payload["fingerprint_changed"])

    def test_the_baseline_against_itself_reports_none(self):
        payload = self.api.drift(BASELINE_LABEL, self.scripted_id)
        self.assertFalse(payload["has_drift"])

    def test_unknown_baseline_names_the_available_ones(self):
        with self.assertRaises(ApiError) as ctx:
            self.api.drift("no-such-baseline", self.scripted_id)
        self.assertEqual(ctx.exception.status, 404)
        self.assertIn(BASELINE_LABEL, ctx.exception.message)


class TestComparison(ApiTestCase):
    def test_endpoints_appear_with_the_labels_given(self):
        payload = self.api.comparison(
            (self.scripted_id, self.bare_id), ("scripted", "bare")
        )
        self.assertEqual([e["label"] for e in payload["endpoints"]], ["scripted", "bare"])

    def test_metric_rows_carry_the_intervals_the_matrix_compared(self):
        # The reason ComparisonMatrix.to_dict gained metric_rows: without the
        # bounds, "these endpoints were not distinguished" cannot be drawn.
        payload = self.api.comparison((self.scripted_id, self.bare_id), ())
        rows = {r["metric"]: r for r in payload["metric_rows"]}
        leak = rows["leak_rate"]
        self.assertTrue(leak["all_overlap"])
        for measurement in leak["by_label"].values():
            self.assertIsNotNone(measurement["ci_low"])
            self.assertIsNotNone(measurement["ci_high"])
            self.assertGreater(measurement["n"], 0)

    def test_undistinguished_metrics_agree_with_metric_rows(self):
        # One rule, one home. If these two ever disagree, the payload is
        # telling the reader two different things about the same run.
        payload = self.api.comparison((self.scripted_id, self.bare_id), ())
        flagged = {
            (r["probe_id"], r["unit"], r["metric"])
            for r in payload["metric_rows"]
            if r["all_overlap"]
        }
        listed = {
            (r["probe_id"], r["unit"], r["metric"])
            for r in payload["undistinguished_metrics"]
        }
        self.assertEqual(flagged, listed)

    def test_counts_are_carried_without_an_interval(self):
        # A count has no interval and must not be drawn as one.
        payload = self.api.comparison((self.scripted_id, self.bare_id), ())
        counts = [
            m
            for r in payload["metric_rows"]
            for m in r["by_label"].values()
            if m["kind"] == "count"
        ]
        self.assertTrue(counts)
        for measurement in counts:
            self.assertIsNone(measurement["ci_low"])
            self.assertIsNone(measurement["ci_high"])

    def test_the_payload_ranks_nothing(self):
        payload = self.api.comparison((self.scripted_id, self.bare_id), ())
        text = json.dumps(payload)
        for forbidden in ('"rank"', '"ranking"', '"winner"', '"score"', '"best"'):
            self.assertNotIn(forbidden, text)

    def test_one_run_is_not_a_comparison(self):
        with self.assertRaises(ApiError) as ctx:
            self.api.comparison((self.scripted_id,), ())
        self.assertEqual(ctx.exception.status, 400)

    def test_the_same_run_twice_is_refused(self):
        with self.assertRaises(ApiError):
            self.api.comparison((self.scripted_id, self.scripted_id), ())

    def test_label_count_must_match_run_count(self):
        with self.assertRaises(ApiError) as ctx:
            self.api.comparison((self.scripted_id, self.bare_id), ("only-one",))
        self.assertEqual(ctx.exception.code, "label-count-mismatch")


class TestCatalogue(ApiTestCase):
    def test_probes_expose_their_auditor_voice_text(self):
        probes = {p["probe_id"]: p for p in self.api.probes()}
        self.assertIn("injection-resistance", probes)
        for field in ("title", "procedure", "population", "limitations", "remediation"):
            self.assertTrue(probes["injection-resistance"][field])

    def test_limitations_are_present_on_every_shipped_probe(self):
        # D-015: the screens are lexical and each one says what that costs.
        # Scoped to the shipped probes because `PROBES` is a process-wide
        # registry and other test modules register throwaway probes into it.
        shipped = {
            "injection-resistance",
            "output-consistency",
            "citation-faithfulness",
        }
        probes = {p["probe_id"]: p for p in self.api.probes()}
        self.assertTrue(shipped <= set(probes))
        for probe_id in sorted(shipped):
            self.assertTrue(probes[probe_id]["limitations"], probe_id)

    def test_suites_list_their_probes(self):
        suites = {s["name"]: s for s in self.api.suites()}
        self.assertIn("baseline-assurance", suites)
        self.assertIn("injection-resistance", suites["baseline-assurance"]["probe_ids"])

    def test_a_mock_fixture_in_suites_is_not_reported_as_a_suite(self):
        # suites/ also holds endpoint fixtures, which are JSON but not specs.
        paths = {s["path"] for s in self.api.suites()}
        self.assertNotIn("suites/demo-endpoint.json", paths)

    def test_meta_reports_schema_versions_and_counts(self):
        meta = self.api.meta()
        self.assertEqual(meta["api_version"], API_VERSION)
        self.assertTrue(meta["read_only"])
        self.assertIn("evidence", meta["engine_schema"])
        self.assertGreaterEqual(meta["decisions_count"], 41)
        self.assertTrue(meta["frameworks"])

    def test_every_catalogued_framework_is_marked_partial(self):
        # D-026: absence of a control is not a statement about it.
        for framework in self.api.meta()["frameworks"]:
            self.assertTrue(framework["partial"], framework["id"])


class TestRagScreenCheck(ApiTestCase):
    def test_the_shipped_dataset_fails_per_category(self):
        payload = self.api.rag_screen_check("northwind-rag-golden.json")
        self.assertEqual(payload["outcome"], "fail")
        failing = set(payload["failing_categories"])
        self.assertEqual(failing, {"paraphrase", "entity-swap", "term-swap"})

    def test_strata_carry_their_own_intervals(self):
        payload = self.api.rag_screen_check("northwind-rag-golden.json")
        for stratum in payload["strata"]:
            self.assertIsNotNone(stratum["accuracy"]["ci_low"])
            self.assertIsNotNone(stratum["accuracy"]["ci_high"])

    def test_unknown_dataset_names_the_available_ones(self):
        with self.assertRaises(ApiError) as ctx:
            self.api.rag_screen_check("nope.json")
        self.assertEqual(ctx.exception.status, 404)
        self.assertIn("northwind-rag-golden.json", ctx.exception.message)


# --- hostile input -----------------------------------------------------------


class TestPathTraversal(ApiTestCase):
    #: The shapes an attacker actually sends, plus the encoded variants that
    #: get past naive string checks.
    HOSTILE = (
        "../DECISIONS",
        "../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        "....//....//etc/passwd",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "..\\..\\Windows\\win.ini",
        "run\x00.json",
        "%2e%2e%2f",
        ".",
        "..",
        "",
    )

    def test_run_ids_reject_everything_but_a_derived_id(self):
        for candidate in self.HOSTILE:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ApiError) as ctx:
                    self.api.run(candidate)
                self.assertEqual(ctx.exception.status, 400)

    def test_baseline_labels_reject_traversal(self):
        for candidate in self.HOSTILE:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ApiError) as ctx:
                    self.api.drift(candidate, self.scripted_id)
                self.assertIn(ctx.exception.status, (400, 404))

    def test_dataset_names_reject_traversal(self):
        for candidate in self.HOSTILE + ("../suites/baseline.json",):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ApiError) as ctx:
                    self.api.rag_screen_check(candidate)
                self.assertEqual(ctx.exception.status, 404)

    def test_a_file_outside_the_dataset_directory_is_unreachable(self):
        # The suite spec is real, readable JSON one directory over. Naming it
        # must fail on the whitelist, not on the parser.
        with self.assertRaises(ApiError) as ctx:
            self.api.rag_screen_check("baseline.json")
        self.assertEqual(ctx.exception.code, "unknown-dataset")


class TestNoSecretsInResponses(ApiTestCase):
    """Nothing the server returns may carry a credential.

    The adapters are careful (D-033); this asserts the API does not undo that
    care by serialising something they kept out.
    """

    SECRET = "sk-ant-test-DO-NOT-LEAK-0123456789"

    def all_payloads(self) -> str:
        parts = [
            json.dumps(self.api.meta()),
            json.dumps(self.api.runs()),
            json.dumps(self.api.run(self.scripted_id)),
            json.dumps(self.api.coverage(self.scripted_id, ("evidence-journal",))),
            json.dumps(self.api.journal_entries(include_payload=True)),
            json.dumps(self.api.journal_verify()),
            json.dumps(self.api.baselines()),
            json.dumps(self.api.drift(BASELINE_LABEL, self.bare_id)),
            json.dumps(self.api.comparison((self.scripted_id, self.bare_id), ())),
            json.dumps(self.api.probes()),
            json.dumps(self.api.suites()),
            json.dumps(self.api.rag_screen_check("northwind-rag-golden.json")),
        ]
        return "\n".join(parts)

    def test_no_credential_shaped_field_appears_anywhere(self):
        # Credential *shapes*, not the word "secret": the injection probe's
        # attack prompts legitimately ask a model what the secret is, and that
        # text is the procedure. Banning the word would fail on real evidence
        # while catching nothing a real leak would look like.
        text = self.all_payloads().lower()
        for forbidden in (
            "api_key",
            "apikey",
            "api-key",
            "authorization",
            "bearer ",
            "sk-ant-",
            "sk-proj-",
            "anthropic_api_key",
            "openai_api_key",
        ):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, text)

    def test_environment_values_do_not_reach_responses(self):
        import os

        os.environ["ANTHROPIC_API_KEY"] = self.SECRET
        try:
            self.assertNotIn(self.SECRET, self.all_payloads())
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_the_scan_actually_fails_on_a_planted_secret(self):
        # A guard that cannot fail proves nothing. Plant a credential in a
        # stored run and confirm the scan above would have caught it.
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            api = build_workspace(workspace)
            run_id = api.runs()[0]["run_id"]
            path = workspace / "runs" / f"{run_id}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["evidence"][0]["config"]["api_key"] = self.SECRET
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")

            text = json.dumps(api.run(run_id)).lower()
            self.assertIn("api_key", text)
            self.assertIn(self.SECRET.lower(), text)


# --- the D-001 guarantee -----------------------------------------------------


class TestStdlibOnly(unittest.TestCase):
    """``serve.py`` may import the standard library and this repo, nothing else.

    Resolved by locating each imported module rather than by consulting a
    version-specific name list, so the check states the thing D-001 actually
    forbids -- an installed package -- and runs on any Python this project
    supports.
    """

    FIRST_PARTY = {
        "adapters",
        "battery",
        "compare",
        "core",
        "drift",
        "frameworks",
        "journal",
        "probes",
        "rag",
        "report",
        "serve",
        "cli",
    }

    #: Where an installed dependency lands, whatever the platform calls it.
    INSTALLED_MARKERS = ("site-packages", "dist-packages", ".egg")

    def imported_roots(self, path: Path) -> set:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        return roots

    def third_party(self, roots: set) -> set:
        """Roots that resolve to an installed package rather than the stdlib."""
        import importlib.util

        offenders = set()
        for root in roots:
            if root in self.FIRST_PARTY or root in sys.builtin_module_names:
                continue
            try:
                spec = importlib.util.find_spec(root)
            except (ImportError, ValueError):
                offenders.add(root)
                continue
            origin = (spec.origin or "") if spec else ""
            if not spec:
                offenders.add(root)
            elif any(marker in origin for marker in self.INSTALLED_MARKERS):
                offenders.add(root)
        return offenders

    def test_serve_imports_nothing_third_party(self):
        roots = self.imported_roots(REPO_ROOT / "serve.py")
        self.assertEqual(self.third_party(roots), set())

    def test_the_fixture_generator_imports_nothing_third_party(self):
        generator = REPO_ROOT / "web" / "tests" / "fixtures" / "generate.py"
        self.assertEqual(self.third_party(self.imported_roots(generator)), set())

    def test_the_check_would_notice_a_new_dependency(self):
        # Same discipline as the planted-secret test: prove the guard fires.
        # unittest itself is stdlib, so a stdlib import must stay clean while
        # a name that cannot resolve at all is reported.
        self.assertEqual(self.third_party({"json", "http", "sqlite3"}), set())
        self.assertEqual(
            self.third_party({"definitely_not_installed_xyz"}),
            {"definitely_not_installed_xyz"},
        )


# --- HTTP surface ------------------------------------------------------------


class TestHttpSurface(unittest.TestCase):
    """The routing layer, exercised over a real socket."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = TemporaryDirectory()
        cls.workspace = Path(cls._tmp.name)
        cls.api = build_workspace(cls.workspace)
        cls.run_id = cls.api.runs()[0]["run_id"]
        cls.httpd = serve(
            root=cls.workspace, port=0, web_dir=cls.workspace / "web", quiet=True
        )
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls._tmp.cleanup()

    def request(self, path: str, method: str = "GET"):
        conn = HTTPConnection(HOST, self.port, timeout=10)
        try:
            conn.request(method, path)
            response = conn.getresponse()
            body = response.read()
            return response.status, dict(response.getheaders()), body
        finally:
            conn.close()

    def get_json(self, path: str):
        status, headers, body = self.request(path)
        return status, headers, json.loads(body) if body else None

    def test_binds_loopback_only(self):
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")

    def test_every_route_answers(self):
        for path in (
            "/api/meta",
            "/api/runs",
            f"/api/runs/{self.run_id}",
            f"/api/runs/{self.run_id}/coverage",
            "/api/journal/entries",
            "/api/journal/verify",
            "/api/baselines",
            "/api/probes",
            "/api/suites",
            "/api/rag/screen-check",
        ):
            with self.subTest(path=path):
                status, _, payload = self.get_json(path)
                self.assertEqual(status, 200)
                self.assertIsNotNone(payload)

    def test_schema_version_travels_in_a_header(self):
        _, headers, _ = self.get_json("/api/meta")
        self.assertIn("X-Engine-Schema", headers)
        self.assertIn(f"api={API_VERSION}", headers["X-Engine-Schema"])

    def test_no_cors_header_is_emitted(self):
        _, headers, _ = self.get_json("/api/runs")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_the_python_version_is_not_advertised(self):
        _, headers, _ = self.get_json("/api/runs")
        self.assertNotIn("Python", headers.get("Server", ""))

    def test_write_methods_are_not_served(self):
        # Read-only by construction: there is no handler to reach.
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            with self.subTest(method=method):
                status, _, _ = self.request("/api/runs", method=method)
                self.assertEqual(status, 501)

    def test_an_unknown_route_returns_a_structured_error(self):
        status, _, payload = self.get_json("/api/nope")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "unknown-route")

    def test_a_bad_run_id_returns_a_structured_error(self):
        status, _, payload = self.get_json("/api/runs/not-a-run-id")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid-run-id")

    def test_traversal_in_the_url_does_not_escape(self):
        for path in (
            "/api/runs/" + quote("../../DECISIONS.md", safe=""),
            "/api/rag/screen-check?dataset=" + quote("../suites/baseline.json", safe=""),
            "/api/drift?baseline=" + quote("../../etc/passwd", safe="") + "&run=" + self.run_id,
        ):
            with self.subTest(path=path):
                status, _, payload = self.get_json(path)
                self.assertIn(status, (400, 404))
                self.assertIn("error", payload)

    def test_comparison_requires_two_runs_over_http(self):
        status, _, payload = self.get_json(f"/api/comparison?runs={self.run_id}")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "too-few-runs")

    def test_static_route_explains_itself_before_the_ui_is_built(self):
        status, _, body = self.request("/")
        self.assertEqual(status, 404)
        self.assertIn(b"has not been built", body)

    def test_head_returns_headers_without_a_body(self):
        status, headers, body = self.request("/api/meta", method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertNotEqual(headers["Content-Length"], "0")


class TestStaticAssets(unittest.TestCase):
    """Serving a built front end, once one exists."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        build_workspace(self.workspace)
        self.web = self.workspace / "dist"
        self.web.mkdir()
        (self.web / "index.html").write_text("<main>app</main>", encoding="utf-8")
        (self.web / "app.js").write_text("export const x = 1;", encoding="utf-8")
        self.httpd = serve(root=self.workspace, port=0, web_dir=self.web, quiet=True)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def request(self, path: str):
        conn = HTTPConnection(HOST, self.port, timeout=10)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_index_is_served_at_the_root(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<main>app</main>", body)
        self.assertIn("text/html", headers["Content-Type"])

    def test_assets_get_their_content_type(self):
        status, headers, _ = self.request("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])

    def test_an_unknown_path_falls_back_to_the_app_shell(self):
        status, _, body = self.request("/runs/abc123")
        self.assertEqual(status, 200)
        self.assertIn(b"<main>app</main>", body)

    def test_a_traversal_request_cannot_read_outside_the_asset_tree(self):
        # Falls back to the app shell rather than escaping to the repo root.
        status, _, body = self.request("/../DECISIONS.md")
        self.assertNotIn(b"Decision Ledger", body)
        self.assertIn(b"<main>app</main>", body)

    def test_sniffing_is_disabled(self):
        _, headers, _ = self.request("/app.js")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")


if __name__ == "__main__":
    unittest.main()
