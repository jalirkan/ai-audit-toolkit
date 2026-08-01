/**
 * Shell and routing.
 *
 * Hash routing, hand-rolled. The view set does not earn a router dependency,
 * and the workstation rule is that dependencies go where rewrites are cheap --
 * this is cheap to replace the moment it stops being enough.
 */

import { useEffect, useState } from "react";
import { ApiError, api } from "./api/client";
import type { DocumentModel } from "./api/document";
import type {
  ComparisonPayload,
  CoveragePayload,
  DriftPayload,
} from "./api/comparison";
import type { BatteryResult, RunSummary } from "./api/schema";
import { Comparison } from "./views/Comparison";
import { Coverage } from "./views/Coverage";
import { Drift } from "./views/Drift";
import { RunDetail } from "./views/RunDetail";
import { RunsIndex } from "./views/RunsIndex";
import { TrialDetail } from "./views/TrialDetail";
import { Workpaper } from "./views/Workpaper";
import {
  JournalView,
  type JournalEntriesPayload,
  type VerificationPayload,
} from "./views/JournalView";

const ID = "([0-9a-f]{16})";

type Route =
  | { name: "runs" }
  | { name: "run"; runId: string }
  | { name: "workpaper"; runId: string }
  | { name: "coverage"; runId: string }
  | { name: "trials"; runId: string; unit: number }
  | { name: "drift"; runId: string; baseline: string }
  | { name: "compare"; runs: string[] }
  | { name: "journal" };

function parseRoute(hash: string): Route {
  let m: RegExpMatchArray | null;
  if ((m = hash.match(new RegExp(`^#/runs/${ID}/workpaper$`))))
    return { name: "workpaper", runId: m[1]! };
  if ((m = hash.match(new RegExp(`^#/runs/${ID}/coverage$`))))
    return { name: "coverage", runId: m[1]! };
  if ((m = hash.match(new RegExp(`^#/runs/${ID}/trials/(\\d+)$`))))
    return { name: "trials", runId: m[1]!, unit: Number(m[2]) };
  if ((m = hash.match(new RegExp(`^#/runs/${ID}/drift/([A-Za-z0-9._-]{1,64})$`))))
    return { name: "drift", runId: m[1]!, baseline: m[2]! };
  if ((m = hash.match(new RegExp(`^#/compare/${ID}/${ID}$`))))
    return { name: "compare", runs: [m[1]!, m[2]!] };
  if (hash === "#/journal") return { name: "journal" };
  if ((m = hash.match(new RegExp(`^#/runs/${ID}$`))))
    return { name: "run", runId: m[1]! };
  return { name: "runs" };
}

const CAPABILITIES = ["evidence-journal", "drift-monitoring", "workpapers"];

export function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [run, setRun] = useState<BatteryResult | null>(null);
  const [workpaper, setWorkpaper] = useState<DocumentModel | null>(null);
  const [coverage, setCoverage] = useState<CoveragePayload | null>(null);
  const [drift, setDrift] = useState<DriftPayload | null>(null);
  const [matrix, setMatrix] = useState<ComparisonPayload | null>(null);
  const [journal, setJournal] = useState<JournalEntriesPayload | null>(null);
  const [verification, setVerification] = useState<VerificationPayload | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onHash = () => {
      setError(null);
      setRoute(parseRoute(window.location.hash));
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const fail = (e: unknown) =>
    setError(e instanceof Error ? e.message : String(e));

  useEffect(() => {
    const c = new AbortController();
    api.runs(c.signal).then(setRuns).catch((e) => {
      if (e instanceof ApiError) setError(e.message);
    });
    return () => c.abort();
  }, []);

  const runId = "runId" in route ? route.runId : null;

  useEffect(() => {
    if (!runId) return void setRun(null);
    const c = new AbortController();
    api.run(runId, c.signal).then(setRun).catch(fail);
    return () => c.abort();
  }, [runId]);

  useEffect(() => {
    const c = new AbortController();
    if (route.name === "workpaper") {
      api.workpaper(route.runId, CAPABILITIES, c.signal).then(setWorkpaper).catch(fail);
    } else setWorkpaper(null);
    if (route.name === "coverage") {
      api.coverage(route.runId, CAPABILITIES, c.signal).then(setCoverage).catch(fail);
    } else setCoverage(null);
    if (route.name === "drift") {
      api.drift(route.baseline, route.runId, c.signal).then(setDrift).catch(fail);
    } else setDrift(null);
    if (route.name === "compare") {
      api.comparison(route.runs, [], c.signal).then(setMatrix).catch(fail);
    } else setMatrix(null);
    if (route.name === "journal") {
      api.journalEntries(200, c.signal).then(setJournal).catch(fail);
    } else {
      setJournal(null);
      setVerification(null);
    }
    return () => c.abort();
  }, [route]);

  const go = (hash: string) => {
    window.location.hash = hash;
  };

  let body = null;
  if (route.name === "workpaper" && workpaper) {
    body = <Workpaper document={workpaper} onBack={() => go(`#/runs/${route.runId}`)} />;
  } else if (route.name === "coverage" && coverage) {
    body = <Coverage coverage={coverage} />;
  } else if (route.name === "drift" && drift) {
    body = <Drift report={drift} />;
  } else if (route.name === "compare" && matrix) {
    body = <Comparison matrix={matrix} />;
  } else if (route.name === "journal" && journal) {
    body = (
      <JournalView
        entries={journal}
        verification={verification}
        verifying={verifying}
        onVerify={(expectHead) => {
          setVerifying(true);
          api
            .journalVerify(expectHead)
            .then(setVerification)
            .catch(fail)
            .finally(() => setVerifying(false));
        }}
      />
    );
  } else if (route.name === "trials" && run) {
    const evidence = run.evidence[route.unit];
    body = evidence ? (
      <TrialDetail evidence={evidence} onBack={() => go(`#/runs/${route.runId}`)} />
    ) : (
      <p className="text-sm text-muted">No such unit in this run.</p>
    );
  } else if (route.name === "run" && run) {
    body = (
      <RunDetail
        run={run}
        onBack={() => go("#/")}
        onOpenWorkpaper={() => go(`#/runs/${run.run_id}/workpaper`)}
        onOpenCoverage={() => go(`#/runs/${run.run_id}/coverage`)}
        onOpenTrials={(unit) => go(`#/runs/${run.run_id}/trials/${unit}`)}
      />
    );
  } else if (route.name === "runs" && runs) {
    body = (
      <>
        <RunsIndex runs={runs} onSelect={(id) => go(`#/runs/${id}`)} />
        {runs.length >= 2 && (
          <p className="mt-8 text-sm text-ink-soft" data-print="hide">
            <button
              className="text-accent hover:underline"
              onClick={() => go(`#/compare/${runs[0]!.run_id}/${runs[1]!.run_id}`)}
            >
              Compare the two most recent runs →
            </button>
          </p>
        )}
      </>
    );
  } else if (!error) {
    body = <p className="text-sm text-muted">Loading evidence…</p>;
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <nav
        className="mb-10 flex items-baseline justify-between border-b border-rule pb-3"
        data-print="hide"
      >
        <a href="#/" className="text-sm font-semibold tracking-tight text-ink">
          Audit evidence
        </a>
        <span className="flex items-baseline gap-4 text-xs text-muted">
          <a href="#/journal" className="hover:text-ink">
            journal
          </a>
          read-only · localhost
        </span>
      </nav>

      {error && (
        <p className="mb-6 border-l-2 border-fail pl-3 text-sm text-fail">{error}</p>
      )}

      {body}
    </div>
  );
}
