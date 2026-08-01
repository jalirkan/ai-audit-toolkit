/**
 * Every stored run, listed.
 *
 * Deliberately absent: any health score, grade, or per-run percentage. The
 * rollup is a distribution of outcomes (D-016) and that is what the row shows
 * -- three distinct marks rather than one status light, so a run with one
 * failure among several passes cannot be read as simply "red".
 *
 * The battery outcome is shown too, because it is a real field the engine
 * computes by precedence, but it sits next to the counts rather than
 * replacing them.
 */

import { useMemo, useState } from "react";
import type { Outcome, RunSummary } from "../api/schema";
import { OutcomeCountsRow, OutcomeTag } from "../design/Outcome";
import { formatTimestamp } from "../lib/format";

type SortKey = "started_at" | "battery" | "units_tested";

export function RunsIndex({
  runs,
  onSelect,
}: {
  runs: RunSummary[];
  onSelect: (runId: string) => void;
}) {
  const [filter, setFilter] = useState<Outcome | "all">("all");
  const [sort, setSort] = useState<SortKey>("started_at");

  const shown = useMemo(() => {
    const filtered =
      filter === "all" ? runs : runs.filter((r) => r.outcome === filter);
    return [...filtered].sort((a, b) => {
      if (sort === "battery") return a.battery.localeCompare(b.battery);
      if (sort === "units_tested") return b.units_tested - a.units_tested;
      return b.started_at.localeCompare(a.started_at);
    });
  }, [runs, filter, sort]);

  return (
    <div>
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Runs</h1>
        <p className="mt-1 max-w-prose text-sm text-ink-soft" data-role="denial">
          Each row is one battery run against one endpoint. Outcomes are counted,
          not averaged: there is no overall score, because a leak rate and an
          agreement rate are not commensurable.
        </p>
      </header>

      <div className="mb-4 flex flex-wrap items-center gap-4" data-print="hide">
        <label className="text-xs uppercase tracking-wider text-muted">
          Outcome{" "}
          <select
            className="ml-1 border border-rule bg-raised px-2 py-1 text-sm text-ink"
            value={filter}
            onChange={(e) => setFilter(e.target.value as Outcome | "all")}
            aria-label="Filter by outcome"
          >
            <option value="all">all</option>
            <option value="fail">fail</option>
            <option value="error">error</option>
            <option value="inconclusive">inconclusive</option>
            <option value="pass">pass</option>
          </select>
        </label>
        <label className="text-xs uppercase tracking-wider text-muted">
          Sort{" "}
          <select
            className="ml-1 border border-rule bg-raised px-2 py-1 text-sm text-ink"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            aria-label="Sort runs"
          >
            <option value="started_at">most recent</option>
            <option value="battery">battery</option>
            <option value="units_tested">units tested</option>
          </select>
        </label>
        <span className="text-xs text-muted">
          {shown.length} of {runs.length} run(s)
        </span>
      </div>

      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-rule-strong text-left text-xs uppercase tracking-wider text-muted">
            <th className="py-2 pr-4 font-medium">Battery</th>
            <th className="py-2 pr-4 font-medium">Endpoint</th>
            <th className="py-2 pr-4 font-medium">Started</th>
            <th className="py-2 pr-4 font-medium">Units</th>
            <th className="py-2 pr-4 font-medium">Outcomes</th>
            <th className="py-2 font-medium">Rollup</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((run) => (
            <tr
              key={run.run_id}
              className="cursor-pointer border-b border-rule align-top hover:bg-accent-soft/60"
              onClick={() => onSelect(run.run_id)}
            >
              <td className="py-3 pr-4">
                <button
                  className="text-left text-ink underline-offset-2 hover:underline"
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelect(run.run_id);
                  }}
                >
                  {run.battery}
                </button>
                <div className="numeric text-xs text-muted">{run.run_id}</div>
              </td>
              <td className="py-3 pr-4">
                <div className="text-ink-soft">
                  {run.fingerprint.adapter}:{run.fingerprint.model}
                </div>
                <div className="numeric text-xs text-muted">
                  {run.total_trials} model call(s)
                </div>
              </td>
              <td className="numeric py-3 pr-4 text-ink-soft">
                {formatTimestamp(run.started_at)}
              </td>
              <td className="numeric py-3 pr-4 text-ink-soft">{run.units_tested}</td>
              <td className="py-3 pr-4">
                <OutcomeCountsRow counts={run.outcome_counts} />
              </td>
              <td className="py-3">
                <OutcomeTag outcome={run.outcome} showMeaning />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {shown.length === 0 && (
        <p className="mt-6 text-sm text-muted">
          No runs match this filter.
        </p>
      )}
    </div>
  );
}
