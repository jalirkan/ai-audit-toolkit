"""Read-only local HTTP API over stored engine output.

This exists so a reviewer can read evidence in a browser and challenge it.
It runs the same procedures nothing: it reads what the CLI already wrote --
run files, the journal, baselines, catalogs -- and hands them over unchanged.

Standard library only, like everything else on the Python side (D-001). A
front end that made the engine un-runnable in a locked-down environment would
have cost more than it added.

## Four properties worth stating plainly

**Read-only by construction, not by policy.** Only GET and HEAD are dispatched;
there is no write path to disable, no POST handler guarded by a flag. Running a
battery costs money against a real endpoint, and a browser is the wrong place
to decide to spend it. Deferred deliberately, not forgotten.

**Payloads are the engine's own ``to_dict()`` output, byte for byte.** Those
shapes are versioned and covered by the existing suite; reshaping them here
would create a second, untested contract that drifts from the first. Where the
engine has no serializer -- ``VerificationResult``, ``JournalEntry`` -- this
module defines one, because there is nothing to preserve, and says so.

**Schema versions travel in a header, not a wrapper.** ``Evidence`` and
``BatteryResult`` carry ``schema_version`` in the payload; ``CoverageReport``,
``DriftReport``, and ``ComparisonMatrix`` do not. Wrapping every response in an
envelope to fix that would reshape the payloads, so the version map goes in
``X-Engine-Schema`` and in ``/api/meta`` instead. The client refuses a version
it does not understand, the same way ``Evidence.from_dict`` does.

**No path parameter is ever joined to a filesystem path.** Run ids are matched
against the exact pattern ``make_run_id`` produces, baseline labels reuse
``drift.baseline.validate_label``, and suites and datasets are resolved by
enumerating their directory and matching a basename exactly. Traversal is not
escaped or filtered; it is unrepresentable, because no attacker-supplied string
reaches ``Path.__truediv__``.

## On authentication

There is none, and none is wanted. The server binds ``127.0.0.1`` and refuses
to bind anything else, so reaching it already requires code execution on the
machine that holds the evidence. A login form in front of a file the user can
open with ``cat`` would be theatre, and audit tooling that performs security it
does not have is worse than tooling that states its boundary. The boundary is
the loopback interface.
"""

from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import battery.runner as battery_runner
import core.evidence as core_evidence
import drift.baseline as drift_baseline
import journal.store as journal_store
from battery.runner import BatteryResult
from battery.spec import BatterySpec
from compare.matrix import ComparisonMatrix, EndpointRun
from drift.baseline import BaselineStore, validate_label
from drift.compare import compare_runs
from frameworks.coverage import build_coverage
from journal.store import Journal
from probes.base import PROBES
from rag.dataset import load_dataset
from rag.harness import run_screen_check

# Importing the package registers every built-in probe, which is what makes
# /api/probes and the coverage mapping see them.
import probes  # noqa: F401  (registration side effect)

#: This module's own contract version, distinct from the engine's record
#: schemas. Bumped when a route or a shape defined *here* changes.
API_VERSION = 1

#: Loopback only. Not configurable: an audit evidence server reachable from
#: the network is a different product with a different threat model, and
#: making it one flag away invites someone to flip it.
HOST = "127.0.0.1"
#: 8765 is taken by the certifications project's study server on this machine,
#: and two localhost tools fighting over a port is a confusing first run.
DEFAULT_PORT = 8770

DEFAULT_RUNS_DIR = "runs"
DEFAULT_JOURNAL = "runs/journal.db"
DEFAULT_BASELINES_DIR = "baselines"
DEFAULT_SUITES_DIR = "suites"
DEFAULT_DATASETS_DIR = "datasets"
DEFAULT_WEB_DIR = "web/dist"

#: ``make_run_id`` returns 16 hex characters. Matching the exact production
#: rather than a permissive pattern means a malformed id is rejected before it
#: reaches any lookup.
RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
CAPABILITY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
LABEL_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

MAX_JOURNAL_LIMIT = 500
DEFAULT_JOURNAL_LIMIT = 100

#: D-017 in one sentence, attached to every verification response so a client
#: cannot render a green tick without the caveat that qualifies it.
CHAIN_LIMITS = (
    "Verification proves the entries have not been edited, deleted, or "
    "reordered since they were written. It cannot detect a full rebuild: "
    "anyone able to write the database file can regenerate every row and hash "
    "into a chain that verifies. Check the head against a value recorded "
    "outside this machine to close that gap."
)

#: Repeated on every framework payload. A mapping asserts relevance, never
#: satisfaction (D-027), and the disclaimer belongs with the data rather than
#: in whichever view remembers to render it.
MAPPING_DISCLAIMER = (
    "A mapping means the procedure produced evidence relevant to the control, "
    "never that the control is satisfied. Controls generally require "
    "governance, policy, and documentation no test harness supplies. Catalogs "
    "are partial: a control's absence is not a statement about it."
)

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".map": "application/json; charset=utf-8",
}

__all__ = [
    "API_VERSION",
    "HOST",
    "DEFAULT_PORT",
    "CHAIN_LIMITS",
    "MAPPING_DISCLAIMER",
    "ApiError",
    "AuditApi",
    "make_handler",
    "serve",
    "main",
]


class ApiError(Exception):
    """A failure with a status, a stable code, and a message worth reading.

    The CLI tells you which baselines exist when you name one that does not.
    Anything less specific here would be a downgrade, so the message carries
    the same detail.
    """

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def payload(self) -> Dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


def _not_found(code: str, message: str) -> ApiError:
    return ApiError(404, code, message)


def _bad_request(code: str, message: str) -> ApiError:
    return ApiError(400, code, message)


def _available(names: Sequence[str]) -> str:
    return ", ".join(names) if names else "none"


class AuditApi:
    """The read side of the engine, addressed by route.

    Every method returns a JSON-serializable object and raises :class:`ApiError`
    for anything a client did wrong. Nothing here writes.
    """

    def __init__(
        self,
        *,
        root: Path,
        runs_dir: str = DEFAULT_RUNS_DIR,
        journal_path: str = DEFAULT_JOURNAL,
        baselines_dir: str = DEFAULT_BASELINES_DIR,
        suites_dir: str = DEFAULT_SUITES_DIR,
        datasets_dir: str = DEFAULT_DATASETS_DIR,
    ) -> None:
        self.root = Path(root).resolve()
        self.runs_dir = self.root / runs_dir
        self.journal_path = self.root / journal_path
        self.baselines_dir = self.root / baselines_dir
        self.suites_dir = self.root / suites_dir
        self.datasets_dir = self.root / datasets_dir

    # -- schema ---------------------------------------------------------------

    def engine_schema(self) -> Dict[str, int]:
        """Record schema versions, so a client can refuse what it cannot read."""
        return {
            "evidence": core_evidence.SCHEMA_VERSION,
            "battery": battery_runner.SCHEMA_VERSION,
            "journal": journal_store.SCHEMA_VERSION,
            "baseline": drift_baseline.SCHEMA_VERSION,
        }

    def schema_header(self) -> str:
        parts = [f"{k}={v}" for k, v in sorted(self.engine_schema().items())]
        return f"api={API_VERSION}; " + "; ".join(parts)

    # -- validation -----------------------------------------------------------

    def _validated_run_id(self, run_id: str) -> str:
        if not RUN_ID_PATTERN.match(run_id or ""):
            raise _bad_request(
                "invalid-run-id",
                f"run id {run_id!r} is not a 16-character hexadecimal "
                "identifier; run ids are derived, not chosen",
            )
        return run_id

    def _validated_label(self, label: str) -> str:
        try:
            return validate_label(label)
        except ValueError as exc:
            raise _bad_request("invalid-baseline-label", str(exc)) from None

    def _whitelisted_file(self, directory: Path, name: str, kind: str) -> Path:
        """Resolve ``name`` by enumeration, never by joining.

        The candidate set is built from what is on disk, and the supplied name
        must equal one of those basenames exactly. A traversal sequence does not
        need to be stripped because it can never match a real filename.
        """
        available = sorted(p.name for p in directory.glob("*.json")) if directory.is_dir() else []
        if name not in available:
            raise _not_found(
                f"unknown-{kind}",
                f"no {kind} named {name!r} in {directory.name}/. "
                f"Available: {_available(available)}",
            )
        return directory / name

    # -- runs -----------------------------------------------------------------

    def _run_ids(self) -> List[str]:
        if not self.runs_dir.is_dir():
            return []
        return sorted(
            p.stem for p in self.runs_dir.glob("*.json") if RUN_ID_PATTERN.match(p.stem)
        )

    def load_run(self, run_id: str) -> BatteryResult:
        self._validated_run_id(run_id)
        path = self.runs_dir / f"{run_id}.json"
        if not path.is_file():
            raise _not_found(
                "unknown-run",
                f"no stored run {run_id!r} in {self.runs_dir.name}/. "
                f"Available: {_available(self._run_ids())}",
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return BatteryResult.from_dict(data)
        except (ValueError, KeyError) as exc:
            raise ApiError(
                500, "unreadable-run", f"stored run {run_id!r} could not be read: {exc}"
            ) from None

    def runs(self) -> List[Dict[str, Any]]:
        """Index of stored runs.

        The one projection in this module, and it is the shape the front-end
        brief specifies: enough to list and filter runs without shipping every
        trial of every run to render an index.
        """
        out: List[Dict[str, Any]] = []
        for run_id in self._run_ids():
            result = self.load_run(run_id)
            out.append(
                {
                    "run_id": result.run_id,
                    "battery": result.battery,
                    "description": result.description,
                    "outcome": result.outcome,
                    "outcome_counts": result.outcome_counts,
                    "started_at": result.started_at,
                    "finished_at": result.finished_at,
                    "fingerprint": result.fingerprint.to_dict(),
                    "units_tested": result.units_tested,
                    "total_trials": result.total_trials,
                    "schema_version": result.schema_version,
                }
            )
        out.sort(key=lambda r: r["started_at"], reverse=True)
        return out

    def run(self, run_id: str) -> Dict[str, Any]:
        return self.load_run(run_id).to_dict()

    def coverage(self, run_id: str, capabilities: Sequence[str]) -> Dict[str, Any]:
        result = self.load_run(run_id)
        for capability in capabilities:
            if not CAPABILITY_PATTERN.match(capability):
                raise _bad_request(
                    "invalid-capability",
                    f"capability {capability!r} is not a lowercase hyphenated name",
                )
        report = build_coverage(result, capabilities=tuple(capabilities))
        payload = report.to_dict()
        # D-027 travels with the data, not with whichever view remembers it.
        payload["disclaimer"] = MAPPING_DISCLAIMER
        return payload

    # -- journal --------------------------------------------------------------

    def _journal(self) -> Journal:
        if not self.journal_path.is_file():
            raise _not_found(
                "no-journal",
                f"no evidence journal at {self.journal_path}. Run a battery with "
                "--journal to create one.",
            )
        return Journal(self.journal_path)

    def journal_entries(
        self,
        *,
        limit: int = DEFAULT_JOURNAL_LIMIT,
        offset: int = 0,
        kind: Optional[str] = None,
        include_payload: bool = False,
    ) -> Dict[str, Any]:
        """A window onto the chain.

        Payloads are omitted unless asked for: an evidence entry carries every
        trial, and an index that shipped them all would move megabytes to draw a
        list of hashes. The hashes themselves are always present, because they
        are what the chain is.
        """
        if kind is not None and kind not in journal_store.KINDS:
            raise _bad_request(
                "invalid-kind",
                f"unknown journal kind {kind!r}; expected one of "
                f"{sorted(journal_store.KINDS)}",
            )
        if limit < 1 or limit > MAX_JOURNAL_LIMIT:
            raise _bad_request(
                "invalid-limit", f"limit must be between 1 and {MAX_JOURNAL_LIMIT}"
            )
        if offset < 0:
            raise _bad_request("invalid-offset", "offset must be non-negative")

        with self._journal() as jrnl:
            entries = jrnl.entries_of_kind(kind) if kind else jrnl.entries()
            head = jrnl.head()
            total = len(entries)
            window = entries[offset : offset + limit]
            rendered = []
            for entry in window:
                item = {
                    "seq": entry.seq,
                    "recorded_at": entry.recorded_at,
                    "kind": entry.kind,
                    "payload_hash": entry.payload_hash,
                    "prev_hash": entry.prev_hash,
                    "entry_hash": entry.entry_hash,
                    "payload_bytes": len(entry.payload),
                }
                if include_payload:
                    item["payload"] = entry.parsed()
                rendered.append(item)

        return {
            "entries": rendered,
            "head": head,
            "total": total,
            "limit": limit,
            "offset": offset,
            "genesis": journal_store.GENESIS_HASH,
            "schema_version": journal_store.SCHEMA_VERSION,
        }

    def journal_verify(self, expect_head: Optional[str] = None) -> Dict[str, Any]:
        if expect_head is not None and not re.match(
            r"^sha256:[0-9a-f]{64}$", expect_head
        ):
            raise _bad_request(
                "invalid-head",
                "an anchored head is 'sha256:' followed by 64 hexadecimal "
                "characters, as printed by `journal verify`",
            )
        with self._journal() as jrnl:
            result = jrnl.verify(expected_head=expect_head)
        return {
            "ok": result.ok,
            "entries_checked": result.entries_checked,
            "head_hash": result.head_hash,
            "expected_head": expect_head,
            "anchored": expect_head is not None,
            "problems": [
                {"seq": p.seq, "code": p.code, "detail": p.detail}
                for p in result.problems
            ],
            "summary": result.summary(),
            # Never omitted. A "verified" badge without this is an overclaim.
            "does_not_prove": CHAIN_LIMITS,
        }

    # -- baselines and drift --------------------------------------------------

    def baselines(self) -> List[Dict[str, Any]]:
        store = BaselineStore(self.baselines_dir)
        out = []
        for label in store.labels():
            baseline = store.load(label)
            out.append(
                {
                    "label": baseline.label,
                    "saved_at": baseline.saved_at,
                    "note": baseline.note,
                    "run_id": baseline.result.run_id,
                    "battery": baseline.result.battery,
                    "outcome": baseline.result.outcome,
                    "fingerprint": baseline.result.fingerprint.to_dict(),
                    "schema_version": baseline.schema_version,
                }
            )
        return out

    def drift(self, baseline_label: str, run_id: str) -> Dict[str, Any]:
        label = self._validated_label(baseline_label)
        store = BaselineStore(self.baselines_dir)
        try:
            baseline = store.load(label)
        except FileNotFoundError as exc:
            raise _not_found("unknown-baseline", str(exc)) from None
        current = self.load_run(run_id)
        report = compare_runs(baseline.result, current, baseline_label=label)
        return report.to_dict()

    # -- comparison -----------------------------------------------------------

    def comparison(
        self, run_ids: Sequence[str], labels: Sequence[str]
    ) -> Dict[str, Any]:
        """Build a matrix from stored runs.

        ``cli.py compare`` writes reports, not a machine-readable matrix, so
        there is no comparison artifact to load. The matrix is composed here
        from runs the caller names -- which is also the more useful behaviour,
        since any two stored runs of one battery can be set side by side after
        the fact.
        """
        if len(run_ids) < 2:
            raise _bad_request(
                "too-few-runs",
                "a comparison needs at least two runs; pass "
                "?runs=<run_id>,<run_id>",
            )
        if labels and len(labels) != len(run_ids):
            raise _bad_request(
                "label-count-mismatch",
                f"{len(labels)} label(s) supplied for {len(run_ids)} run(s)",
            )
        for label in labels:
            if not LABEL_TOKEN_PATTERN.match(label):
                raise _bad_request(
                    "invalid-label",
                    f"endpoint label {label!r} must be 1-64 characters of "
                    "letters, digits, dot, underscore, or hyphen",
                )
        if len(set(run_ids)) != len(run_ids):
            raise _bad_request(
                "duplicate-runs", "the same run cannot appear twice in a comparison"
            )

        results = [self.load_run(rid) for rid in run_ids]
        batteries = {r.battery for r in results}
        if len(batteries) > 1:
            raise _bad_request(
                "mixed-batteries",
                "these runs are of different batteries "
                f"({_available(sorted(batteries))}); comparing them would put "
                "different procedures in the same column",
            )

        chosen = list(labels) if labels else [r.run_id for r in results]
        if len(set(chosen)) != len(chosen):
            raise _bad_request("duplicate-labels", "endpoint labels must be distinct")

        endpoints = tuple(
            EndpointRun(
                label=label,
                description=(
                    f"{r.fingerprint.adapter}:{r.fingerprint.model} "
                    f"({r.fingerprint.short()})"
                ),
                result=r,
            )
            for label, r in zip(chosen, results)
        )
        matrix = ComparisonMatrix(battery=results[0].battery, endpoints=endpoints)
        return matrix.to_dict()

    # -- catalogue ------------------------------------------------------------

    def probes(self) -> List[Dict[str, Any]]:
        """Probe metadata as the classes declare it, rendered verbatim.

        ``procedure``, ``population``, ``limitations``, and ``remediation`` are
        the auditor-voice text the workpapers already print. The API repeats it
        rather than paraphrasing so both surfaces say the same thing.
        """
        return [
            {
                "probe_id": probe_id,
                "title": cls.title,
                "procedure": cls.procedure,
                "population": cls.population,
                "limitations": cls.limitations,
                "remediation": cls.remediation,
            }
            for probe_id, cls in sorted(PROBES.items())
        ]

    def suites(self) -> List[Dict[str, Any]]:
        """Battery specs on disk.

        ``suites/`` also holds mock endpoint fixtures, which are JSON but not
        batteries, so anything that fails to parse as a spec is skipped rather
        than reported as a broken suite.
        """
        out = []
        if not self.suites_dir.is_dir():
            return out
        for path in sorted(self.suites_dir.glob("*.json")):
            try:
                spec = BatterySpec.load(path)
            except (ValueError, KeyError):
                continue
            out.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "probe_ids": spec.probe_ids,
                    "probe_count": len(spec.probes),
                    "path": f"{self.suites_dir.name}/{path.name}",
                    "schema_version": spec.schema_version,
                }
            )
        return out

    def rag_screen_check(self, dataset_name: str) -> Dict[str, Any]:
        path = self._whitelisted_file(self.datasets_dir, dataset_name, "dataset")
        try:
            dataset = load_dataset(path)
        except ValueError as exc:
            raise ApiError(500, "unreadable-dataset", str(exc)) from None
        return run_screen_check(dataset).to_dict()

    def meta(self) -> Dict[str, Any]:
        from frameworks.catalog import load_frameworks

        frameworks = [
            {
                "id": fw.id,
                "name": fw.name,
                "publication": fw.publication,
                "partial": fw.partial,
                "ids_verified": fw.ids_verified,
                "control_count": len(fw.controls),
                "citation": fw.citation(),
            }
            for _, fw in sorted(load_frameworks().items())
        ]
        return {
            "api_version": API_VERSION,
            "engine_schema": self.engine_schema(),
            "frameworks": frameworks,
            "decisions_count": self._decisions_count(),
            "probe_count": len(PROBES),
            "read_only": True,
            "host": HOST,
            "mapping_disclaimer": MAPPING_DISCLAIMER,
            "paths": {
                "runs": self.runs_dir.name,
                "journal": str(self.journal_path.relative_to(self.root))
                if self.journal_path.is_relative_to(self.root)
                else str(self.journal_path),
                "baselines": self.baselines_dir.name,
                "suites": self.suites_dir.name,
                "datasets": self.datasets_dir.name,
            },
        }

    def _decisions_count(self) -> int:
        path = self.root / "DECISIONS.md"
        if not path.is_file():
            return 0
        return len(
            re.findall(r"^## D-\d+", path.read_text(encoding="utf-8"), re.MULTILINE)
        )


# --- routing -----------------------------------------------------------------

Route = Tuple[re.Pattern, Callable[[AuditApi, Dict[str, str], Dict[str, List[str]]], Any]]


def _one(query: Dict[str, List[str]], key: str, default: Optional[str] = None) -> Optional[str]:
    values = query.get(key)
    if not values:
        return default
    return values[-1]


def _csv(query: Dict[str, List[str]], key: str) -> List[str]:
    raw = _one(query, key)
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _int(query: Dict[str, List[str]], key: str, default: int) -> int:
    raw = _one(query, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise _bad_request("invalid-integer", f"{key} must be an integer, got {raw!r}") from None


def _flag(query: Dict[str, List[str]], key: str) -> bool:
    raw = _one(query, key)
    return raw is not None and raw.lower() in {"1", "true", "yes"}


ROUTES: List[Route] = [
    (re.compile(r"^/api/meta$"), lambda api, m, q: api.meta()),
    (re.compile(r"^/api/runs$"), lambda api, m, q: api.runs()),
    (
        re.compile(r"^/api/runs/(?P<run_id>[^/]+)$"),
        lambda api, m, q: api.run(m["run_id"]),
    ),
    (
        re.compile(r"^/api/runs/(?P<run_id>[^/]+)/coverage$"),
        lambda api, m, q: api.coverage(m["run_id"], _csv(q, "capabilities")),
    ),
    (
        re.compile(r"^/api/journal/entries$"),
        lambda api, m, q: api.journal_entries(
            limit=_int(q, "limit", DEFAULT_JOURNAL_LIMIT),
            offset=_int(q, "offset", 0),
            kind=_one(q, "kind"),
            include_payload=_flag(q, "include_payload"),
        ),
    ),
    (
        re.compile(r"^/api/journal/verify$"),
        lambda api, m, q: api.journal_verify(_one(q, "expect_head")),
    ),
    (re.compile(r"^/api/baselines$"), lambda api, m, q: api.baselines()),
    (
        re.compile(r"^/api/drift$"),
        lambda api, m, q: api.drift(_one(q, "baseline", "") or "", _one(q, "run", "") or ""),
    ),
    (
        re.compile(r"^/api/comparison$"),
        lambda api, m, q: api.comparison(_csv(q, "runs"), _csv(q, "labels")),
    ),
    (re.compile(r"^/api/probes$"), lambda api, m, q: api.probes()),
    (re.compile(r"^/api/suites$"), lambda api, m, q: api.suites()),
    (
        re.compile(r"^/api/rag/screen-check$"),
        lambda api, m, q: api.rag_screen_check(
            _one(q, "dataset", "northwind-rag-golden.json") or ""
        ),
    ),
]


def make_handler(api: AuditApi, web_dir: Optional[Path] = None, *, quiet: bool = False):
    """Build a request handler bound to one :class:`AuditApi`."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "ai-audit-toolkit"
        sys_version = ""  # do not advertise the Python version
        protocol_version = "HTTP/1.1"

        # -- responses --------------------------------------------------------

        def _send(
            self,
            status: int,
            body: bytes,
            content_type: str,
            *,
            extra: Optional[Dict[str, str]] = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # No CORS headers at all: one origin serves both the API and the
            # assets, so there is no cross-origin case to permit.
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, indent=2, allow_nan=False).encode("utf-8")
            self._send(
                status,
                body,
                "application/json; charset=utf-8",
                extra={"X-Engine-Schema": api.schema_header()},
            )

        def _send_error_payload(self, error: ApiError) -> None:
            self._send_json(error.status, error.payload())

        # -- dispatch ---------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            self._dispatch()

        def do_HEAD(self) -> None:  # noqa: N802
            self._dispatch()

        def _dispatch(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query, keep_blank_values=True)

            if path.startswith("/api/"):
                self._dispatch_api(path, query)
                return
            self._serve_static(path)

        def _dispatch_api(self, path: str, query: Dict[str, List[str]]) -> None:
            for pattern, handler in ROUTES:
                match = pattern.match(path)
                if not match:
                    continue
                try:
                    payload = handler(api, match.groupdict(), query)
                except ApiError as exc:
                    self._send_error_payload(exc)
                    return
                except Exception as exc:  # pragma: no cover - defensive
                    self._send_error_payload(
                        ApiError(500, "internal-error", str(exc))
                    )
                    return
                self._send_json(200, payload)
                return
            self._send_error_payload(
                _not_found(
                    "unknown-route",
                    f"no API route matches {path!r}. See /api/meta.",
                )
            )

        # -- static -----------------------------------------------------------

        def _serve_static(self, path: str) -> None:
            if web_dir is None or not web_dir.is_dir():
                self._send(
                    404,
                    (
                        "The API is running. The web front end has not been "
                        "built yet: run `npm install && npm run build` in web/, "
                        "or use the Vite dev server during development.\n"
                    ).encode("utf-8"),
                    "text/plain; charset=utf-8",
                )
                return

            relative = path.lstrip("/") or "index.html"
            candidate = (web_dir / relative).resolve()
            # A built asset tree is enumerated by the build, not by a user, so
            # containment is checked rather than whitelisted -- but it is still
            # checked, because the path came off the wire.
            if not candidate.is_relative_to(web_dir.resolve()) or not candidate.is_file():
                candidate = web_dir / "index.html"  # SPA fallback
                if not candidate.is_file():
                    self._send(404, b"not found\n", "text/plain; charset=utf-8")
                    return
            body = candidate.read_bytes()
            content_type = CONTENT_TYPES.get(
                candidate.suffix, "application/octet-stream"
            )
            self._send(200, body, content_type)

        # -- logging ----------------------------------------------------------

        def log_message(self, fmt: str, *args: Any) -> None:
            if quiet:
                return
            sys.stderr.write(
                "%s - %s\n" % (self.address_string(), fmt % args)
            )

    return Handler


def serve(
    *,
    root: Optional[Path] = None,
    port: int = DEFAULT_PORT,
    web_dir: Optional[Path] = None,
    quiet: bool = False,
) -> ThreadingHTTPServer:
    """Create a bound server. The caller runs it."""
    base = Path(root) if root is not None else Path(__file__).resolve().parent
    api = AuditApi(root=base)
    assets = web_dir if web_dir is not None else base / DEFAULT_WEB_DIR
    return ThreadingHTTPServer((HOST, port), make_handler(api, assets, quiet=quiet))


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Read-only local server over stored audit evidence."
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--root", default="", help="repository root (default: here)")
    parser.add_argument("--web", default="", help=f"built assets (default: {DEFAULT_WEB_DIR})")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent
    web = Path(args.web).resolve() if args.web else root / DEFAULT_WEB_DIR

    httpd = serve(root=root, port=args.port, web_dir=web)
    print(f"Serving audit evidence from {root} on http://{HOST}:{args.port}")
    print("Read-only. Bound to loopback; not reachable from the network.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
