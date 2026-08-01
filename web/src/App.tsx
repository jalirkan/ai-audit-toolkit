/**
 * Shell and routing.
 *
 * Hash routing, hand-rolled. Four views do not earn a router dependency, and
 * the workstation rule is that dependencies go where rewrites are cheap --
 * this is cheap to replace the moment it stops being enough.
 */

import { useEffect, useState } from "react";
import { ApiError, api } from "./api/client";
import type { DocumentModel } from "./api/document";
import type { BatteryResult, RunSummary } from "./api/schema";
import { RunDetail } from "./views/RunDetail";
import { RunsIndex } from "./views/RunsIndex";
import { TrialDetail } from "./views/TrialDetail";
import { Workpaper } from "./views/Workpaper";

type Route =
  | { name: "runs" }
  | { name: "run"; runId: string }
  | { name: "workpaper"; runId: string }
  | { name: "trials"; runId: string; unit: number };

function parseRoute(hash: string): Route {
  const workpaper = hash.match(/^#\/runs\/([0-9a-f]{16})\/workpaper$/);
  if (workpaper) return { name: "workpaper", runId: workpaper[1]! };
  const trials = hash.match(/^#\/runs\/([0-9a-f]{16})\/trials\/(\d+)$/);
  if (trials) return { name: "trials", runId: trials[1]!, unit: Number(trials[2]) };
  const run = hash.match(/^#\/runs\/([0-9a-f]{16})$/);
  if (run) return { name: "run", runId: run[1]! };
  return { name: "runs" };
}

export function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [run, setRun] = useState<BatteryResult | null>(null);
  const [workpaper, setWorkpaper] = useState<DocumentModel | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onHash = () => {
      setError(null);
      setRoute(parseRoute(window.location.hash));
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    api
      .runs(controller.signal)
      .then(setRuns)
      .catch((e) => {
        if (e instanceof ApiError) setError(e.message);
      });
    return () => controller.abort();
  }, []);

  const runId = "runId" in route ? route.runId : null;

  useEffect(() => {
    if (!runId) {
      setRun(null);
      return;
    }
    const controller = new AbortController();
    api
      .run(runId, controller.signal)
      .then(setRun)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    return () => controller.abort();
  }, [runId]);

  useEffect(() => {
    if (route.name !== "workpaper") {
      setWorkpaper(null);
      return;
    }
    const controller = new AbortController();
    api
      .workpaper(route.runId, ["evidence-journal", "workpapers"], controller.signal)
      .then(setWorkpaper)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    return () => controller.abort();
  }, [route]);

  const go = (hash: string) => {
    window.location.hash = hash;
  };

  let body = null;
  if (route.name === "workpaper" && workpaper) {
    body = (
      <Workpaper document={workpaper} onBack={() => go(`#/runs/${route.runId}`)} />
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
        onOpenTrials={(unit) => go(`#/runs/${run.run_id}/trials/${unit}`)}
      />
    );
  } else if (route.name === "runs" && runs) {
    body = <RunsIndex runs={runs} onSelect={(id) => go(`#/runs/${id}`)} />;
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
        <span className="text-xs text-muted">read-only · localhost</span>
      </nav>

      {error && (
        <p className="mb-6 border-l-2 border-fail pl-3 text-sm text-fail">{error}</p>
      )}

      {body}
    </div>
  );
}
