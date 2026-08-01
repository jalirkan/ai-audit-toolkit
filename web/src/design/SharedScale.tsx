/**
 * Several intervals on one axis — the mark that shows what a run did *not*
 * establish.
 *
 * This is the visual argument behind D-036. Handed point estimates, a reader
 * will order them: 1/20 against 2/20 looks like a twofold difference and is
 * not distinguishable at all. Drawing both intervals on a shared axis makes
 * the overlap the thing you see first, so "these endpoints have not been shown
 * to differ" is something the reader observes rather than something the tool
 * asserts.
 *
 * The scale is shared by construction. Each row is positioned against one
 * domain computed across every measurement in the group, because two intervals
 * drawn on separately-zoomed axes are not a comparison — they are two pictures
 * that happen to sit near each other.
 *
 * `overlapping` comes from the engine (`MetricRow.all_overlap`), never from a
 * recomputation here. The rule that decides what a run established has one
 * home, and it is in Python.
 */

import type { Measurement } from "../api/schema";
import { renderMeasurement } from "../lib/format";

export interface ScaleRow {
  label: string;
  measurement: Measurement;
  /** Outcome for this row where one applies; drives fill and shape. */
  outcome?: "pass" | "fail" | "inconclusive" | "error";
}

function domainFor(rows: ScaleRow[], threshold?: number | null) {
  const points: number[] = [];
  for (const row of rows) {
    const m = row.measurement;
    if (!(m.n > 0)) continue;
    points.push(m.value);
    if (m.ci_low !== null) points.push(m.ci_low);
    if (m.ci_high !== null) points.push(m.ci_high);
  }
  if (threshold !== null && threshold !== undefined) points.push(threshold);
  if (points.length === 0) return { min: 0, max: 1 };
  const max = Math.max(...points);
  const proportion = rows[0]?.measurement.kind === "proportion";
  let top = max * 1.2;
  if (proportion) top = Math.min(1, Math.max(top, 0.1));
  else top = Math.max(top, max + 1);
  return { min: 0, max: top || 1 };
}

const FILL: Record<string, string> = {
  pass: "bg-pass",
  fail: "bg-fail",
  inconclusive:
    "hatch-absence text-inconclusive border border-dashed border-inconclusive bg-transparent",
  error: "bg-transparent border border-dotted border-error",
  neutral: "bg-ink/70",
};

export function SharedScale({
  rows,
  threshold,
  overlapping = false,
  caption,
}: {
  rows: ScaleRow[];
  threshold?: number | null;
  /** As the engine reported it. Not recomputed here. */
  overlapping?: boolean;
  caption?: string;
}) {
  const usable = rows.filter((r) => r.measurement.n > 0);
  const domain = domainFor(rows, threshold);
  const span = domain.max - domain.min || 1;
  const pos = (v: number) => Math.min(100, Math.max(0, ((v - domain.min) / span) * 100));

  return (
    <div
      data-testid="shared-scale"
      data-overlapping={overlapping ? "true" : "false"}
      className={
        overlapping
          ? "border-l-2 border-inconclusive/50 pl-4"
          : "border-l-2 border-transparent pl-4"
      }
    >
      {caption && (
        <p className="mb-3 max-w-prose text-sm text-ink-soft">{caption}</p>
      )}

      <div className="space-y-3">
        {rows.map((row) => {
          const m = row.measurement;
          const outcome = row.outcome ?? "neutral";
          if (!(m.n > 0)) {
            return (
              <div key={row.label} className="grid gap-1 sm:grid-cols-[8rem_1fr]">
                <div className="text-sm text-ink">{row.label}</div>
                <div className="numeric text-xs text-muted">not tested (n=0)</div>
              </div>
            );
          }
          const hasInterval = m.ci_low !== null && m.ci_high !== null;
          return (
            <div
              key={row.label}
              className="grid gap-1 sm:grid-cols-[8rem_1fr] sm:items-center"
              data-testid="scale-row"
              data-label={row.label}
            >
              <div className="text-sm text-ink">{row.label}</div>
              <div>
                <div className="relative h-4 w-full bg-rule/40">
                  {threshold !== null && threshold !== undefined && (
                    <div
                      aria-hidden="true"
                      data-testid="shared-threshold"
                      className="absolute inset-y-0 w-px bg-accent"
                      style={{ left: `${pos(threshold)}%` }}
                    />
                  )}
                  {hasInterval && (
                    <div
                      data-testid="scale-bar"
                      role="img"
                      aria-label={`${row.label}: ${renderMeasurement(m)}`}
                      className={`absolute top-1/2 h-2 -translate-y-1/2 rounded-[1px] ${FILL[outcome]}`}
                      style={{
                        left: `${pos(m.ci_low!)}%`,
                        width: `${Math.max(pos(m.ci_high!) - pos(m.ci_low!), 0.6)}%`,
                      }}
                    />
                  )}
                  <div
                    aria-hidden="true"
                    className={`absolute top-1/2 h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full ${
                      outcome === "inconclusive"
                        ? "border-2 border-inconclusive bg-paper"
                        : FILL[outcome]
                    }`}
                    style={{ left: `${pos(m.value)}%` }}
                  />
                </div>
                {/* The figure always travels with the mark. */}
                <div className="mt-0.5 numeric text-xs text-ink-soft">
                  {renderMeasurement(m)}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-2 flex justify-between text-[10px] text-muted numeric">
        <span data-role="axis">{domain.min.toFixed(2)}</span>
        <span data-role="axis">
          shared scale · {usable.length} of {rows.length} reported
        </span>
        <span data-role="axis">{domain.max.toFixed(2)}</span>
      </div>
    </div>
  );
}
