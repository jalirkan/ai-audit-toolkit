/**
 * Endpoints side by side, with no winner column.
 *
 * The section that matters is "not distinguished by this run". A comparison
 * table invites ordering, so wherever every interval overlaps every other, the
 * intervals are drawn together on one axis under a heading that says the run
 * has not shown the endpoints to differ. That is the most useful thing this
 * screen can tell a reader, and it is the thing a conventional dashboard would
 * omit (D-036).
 *
 * There is no ranking, no per-endpoint score, and no total. Which metric
 * matters depends on the workload, and that is the reader's judgment to make.
 */

import type { ComparisonPayload, MetricRowPayload } from "../api/comparison";
import { SharedScale, type ScaleRow } from "../design/SharedScale";
import { Caveat, OutcomeCountsRow, OutcomeTag } from "../design/Outcome";
import { metricLabel, renderMeasurement } from "../lib/format";
import type { Outcome } from "../api/schema";

function rowsFor(row: MetricRowPayload, labels: string[]): ScaleRow[] {
  return labels
    .filter((label) => row.by_label[label])
    .map((label) => ({ label, measurement: row.by_label[label]! }));
}

export function Comparison({ matrix }: { matrix: ComparisonPayload }) {
  const labels = matrix.endpoints.map((e) => e.label);
  const undistinguished = matrix.metric_rows.filter((r) => r.all_overlap);
  const separated = matrix.metric_rows.filter(
    (r) => !r.all_overlap && r.by_label && Object.keys(r.by_label).length > 1,
  );

  return (
    <div>
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">
          {matrix.battery} — {matrix.endpoints.length} endpoints
        </h1>
        <Caveat>
          No overall ranking is produced and no endpoint carries a score. A leak
          rate and an agreement rate are not commensurable, and which one
          matters depends on the workload — that is the reader's judgment, not
          the tool's.
        </Caveat>
      </header>

      <section className="mb-10">
        <h2 className="mb-4 text-sm font-semibold uppercase tracking-[0.12em] text-muted">
          Endpoints
        </h2>
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-rule-strong text-left text-xs uppercase tracking-wider text-muted">
              <th className="py-2 pr-4 font-medium">Label</th>
              <th className="py-2 pr-4 font-medium">Endpoint</th>
              <th className="py-2 pr-4 font-medium">Outcomes</th>
              <th className="py-2 pr-4 font-medium">Rollup</th>
              <th className="py-2 font-medium">Calls</th>
            </tr>
          </thead>
          <tbody>
            {matrix.endpoints.map((e) => (
              <tr key={e.label} className="border-b border-rule align-top">
                <td className="py-3 pr-4 text-ink">{e.label}</td>
                <td className="py-3 pr-4 text-ink-soft">
                  {e.fingerprint.adapter}:{e.fingerprint.model}
                </td>
                <td className="py-3 pr-4">
                  <OutcomeCountsRow counts={e.outcome_counts} />
                </td>
                <td className="py-3 pr-4">
                  <OutcomeTag outcome={e.outcome as Outcome} showMeaning />
                </td>
                <td className="numeric py-3 text-ink-soft">
                  {e.total_calls}
                  <div className="text-[10px] text-muted">
                    {/* D-037: absence is not zero. */}
                    {e.tokens.calls_with_usage} of {e.tokens.calls} reported usage
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* The screen's reason for existing. */}
      <section className="mb-10" data-testid="undistinguished-section">
        <h2 className="text-sm font-semibold uppercase tracking-[0.12em] text-muted">
          Not distinguished by this run
        </h2>
        {undistinguished.length === 0 ? (
          <p className="mt-3 max-w-prose text-sm text-ink-soft">
            Every metric separated the endpoints: on each one, at least one
            interval fails to overlap another.
          </p>
        ) : (
          <>
            <p className="mt-2 max-w-prose text-sm text-ink-soft">
              On each metric below, every endpoint's interval overlaps every
              other's. The run has not shown these endpoints to differ. Ordering
              them by point estimate would invent a difference the sample does
              not support.
            </p>
            <div className="mt-5 space-y-8">
              {undistinguished.map((row) => (
                <div key={`${row.probe_id}-${row.unit}-${row.metric}`}>
                  <h3 className="mb-2 text-sm text-ink">
                    {metricLabel(row.metric)}
                    <span className="ml-2 numeric text-xs text-muted">
                      {row.probe_id} / {row.unit}
                    </span>
                  </h3>
                  <SharedScale
                    rows={rowsFor(row, labels)}
                    overlapping
                    caption="Both intervals on one axis — the overlap is the finding."
                  />
                </div>
              ))}
            </div>
          </>
        )}
      </section>

      <section className="mb-10">
        <h2 className="text-sm font-semibold uppercase tracking-[0.12em] text-muted">
          Metrics that did separate the endpoints
        </h2>
        <div className="mt-5 space-y-8">
          {separated.map((row) => (
            <div key={`${row.probe_id}-${row.unit}-${row.metric}`}>
              <h3 className="mb-2 text-sm text-ink">
                {metricLabel(row.metric)}
                <span className="ml-2 numeric text-xs text-muted">
                  {row.probe_id} / {row.unit}
                </span>
              </h3>
              {row.by_label[labels[0]!]?.kind === "count" ? (
                <ul className="numeric text-xs text-ink-soft">
                  {labels.map((label) =>
                    row.by_label[label] ? (
                      <li key={label}>
                        {label}: {renderMeasurement(row.by_label[label]!)}
                      </li>
                    ) : null,
                  )}
                </ul>
              ) : (
                <SharedScale rows={rowsFor(row, labels)} />
              )}
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="mb-4 text-sm font-semibold uppercase tracking-[0.12em] text-muted">
          Outcomes by unit
        </h2>
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-rule-strong text-left text-xs uppercase tracking-wider text-muted">
              <th className="py-2 pr-4 font-medium">Unit</th>
              {labels.map((label) => (
                <th key={label} className="py-2 pr-4 font-medium">
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.units.map((unit) => (
              <tr
                key={`${unit.probe_id}-${unit.unit}`}
                className="border-b border-rule align-top"
              >
                <td className="py-3 pr-4">
                  <div className="text-ink">{unit.probe_id}</div>
                  <div className="numeric text-xs text-muted">{unit.unit}</div>
                </td>
                {labels.map((label) => (
                  <td key={label} className="py-3 pr-4">
                    <OutcomeTag outcome={unit.outcomes[label] as Outcome} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
