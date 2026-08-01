/**
 * Shell and routing.
 *
 * Hash routing, hand-rolled: two views do not earn a router dependency, and
 * the brief's rule is that dependencies go where rewrites are cheap. This is
 * cheap to replace when F2 adds workpapers and trials.
 */

import { useEffect, useState } from "react";
import { ApiError, api } from "./api/client";
import type { BatteryResult, RunSummary } from "./api/schema";
import { RunDetail } from "./views/RunDetail";
import { RunsIndex } from "./views/RunsIndex";

function currentRunId(): string | null {
  const match = window.location.hash.match(/^#\/runs\/([0-9a-f]{16})$/);
  return match ? match[1]! : null;
}

export function App() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [run, setRun] = useState<BatteryResult | null>(null);
  const [runId, setRunId] = useState<string | null>(currentRunId());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onHash = () => setRunId(currentRunId());
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

  const select = (id: string) => {
    window.location.hash = `#/runs/${id}`;
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <nav className="mb-10 flex items-baseline justify-between border-b border-rule pb-3" data-print="hide">
        <a href="#/" className="text-sm font-semibold tracking-tight text-ink">
          Audit evidence
        </a>
        <span className="text-xs text-muted">read-only · localhost</span>
      </nav>

      {error && (
        <p className="mb-6 border-l-2 border-fail pl-3 text-sm text-fail">{error}</p>
      )}

      {run ? (
        <RunDetail run={run} onBack={() => (window.location.hash = "#/")} />
      ) : runs ? (
        <RunsIndex runs={runs} onSelect={select} />
      ) : (
        !error && <p className="text-sm text-muted">Loading evidence…</p>
      )}
    </div>
  );
}
