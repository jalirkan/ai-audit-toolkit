/**
 * Baseline versus current, per metric, on a shared scale.
 *
 * Drift is significance, not deltas (D-022): a metric counts as drifted only
 * when the interval for the *difference* excludes zero. So the two intervals
 * are drawn on one axis and the difference interval is shown with zero marked
 * — a reader can see whether the movement is more than sampling variation
 * would explain, rather than being handed a delta and left to guess.
 *
 * Two things are stated rather than implied. A worsened outcome is drift even
 * without significance (D-023), because 0/22 to 1/22 under a zero-tolerance
 * control flips pass to fail while being nowhere near significant. And a
 * comparison that is not like-for-like says so (D-024) — units added or
 * removed, configs changed, or a different model fingerprint.
 */

import type { DriftPayload, MetricComparisonPayload } from "../api/comparison";
import { SharedScale } from "../design/SharedScale";
import { Caveat, OutcomeTag } from "../design/Outcome";
import { metricLabel } from "../lib/format";
import type { Outcome } from "../api/schema";

function DifferenceBar({ metric }: { metric: MetricComparisonPayload }) {
  const interval = metric.interval;
  if (!interval) return null;
  // Symmetric domain around zero so "excludes zero" is visually obvious.
  const extent = Math.max(Math.abs(interval.low), Math.abs(interval.high), 0.05) * 1.15;
  const pos = (v: number) => ((v + extent) / (2 * extent)) * 100;
  const excludesZero = interval.low > 0 || interval.high < 0;

  return (
    <div className="mt-2">
      <div className="mb-1 text-xs uppercase tracking-wider text-muted">
        Difference (current − baseline)
      </div>
      <div className="relative h-4 w-full bg-rule/40">
        <div
          aria-hidden="true"
          data-testid="zero-mark"
          className="absolute inset-y-0 w-px bg-accent"
          style={{ left: "50%" }}
        />
        <div
          data-testid="difference-bar"
          data-excludes-zero={excludesZero ? "true" : "false"}
          role="img"
          aria-label={`difference ${interval.point.toFixed(3)}, ${Math.round(
            interval.confidence * 100,
          )}% CI [${interval.low.toFixed(3)}, ${interval.high.toFixed(3)}]`}
          className={`absolute top-1/2 h-2 -translate-y-1/2 rounded-[1px] ${
            excludesZero ? "bg-fail" : "hatch-absence border border-dashed border-inconclusive text-inconclusive"
          }`}
          style={{
            left: `${pos(interval.low)}%`,
            width: `${Math.max(pos(interval.high) - pos(interval.low), 0.6)}%`,
          }}
        />
      </div>
      {/*
        Both arms' sample sizes travel with the difference. A difference has no
        single n, and quoting only the resample count would let a reader take
        10,000 for the evidence base when the evidence base is 22 and 22. The
        seed is printed because an auditor re-running the analysis must get the
        same numbers (D-022).
      */}
      <div className="mt-0.5 text-xs text-ink-soft">
        <span className="numeric">
          {interval.point.toFixed(3)} ({Math.round(interval.confidence * 100)}% CI [
          {interval.low.toFixed(3)}, {interval.high.toFixed(3)}], baseline n=
          {metric.baseline.n}, current n={metric.current.n}; bootstrap ·{" "}
          {interval.resamples} resamples · seed {interval.seed})
          {interval.widened ? " · widened to the analytic bound" : ""}
        </span>
        {/*
          The engine's detail sentence quotes the raw delta. It renders inside
          this block rather than in one of its own so the delta can never be
          read apart from the interval that qualifies it -- reporting deltas
          alone is exactly what D-022 exists to prevent.
        */}
        {metric.detail && (
          <span className="ml-2 text-muted" data-testid="drift-detail">
            {metric.detail}
          </span>
        )}
      </div>
      <p className="mt-1 max-w-prose text-xs text-muted">
        {excludesZero
          ? "The interval for the difference excludes zero, so the movement is more than sampling variation would explain."
          : "The interval for the difference includes zero. This run has not shown the rate to have changed."}
      </p>
    </div>
  );
}

export function Drift({ report }: { report: DriftPayload }) {
  const fingerprintFields = ["adapter", "model", "system_prompt_hash"] as const;
  const changed = fingerprintFields.filter(
    (f) => report.baseline_fingerprint[f] !== report.current_fingerprint[f],
  );

  return (
    <div>
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">
          {report.has_drift ? "Drift detected" : "No drift detected"}
        </h1>
        <p className="mt-1 numeric text-sm text-muted">
          run {report.current_run_id} against baseline {report.baseline_label} (
          {report.baseline_run_id})
        </p>

        {!report.comparable && (
          <div className="mt-4" data-testid="not-like-for-like">
            <Caveat>
              These runs are not like-for-like. Units were added, removed, or
              reconfigured since the baseline, so a difference here may reflect a
              changed procedure rather than changed behaviour.
            </Caveat>
          </div>
        )}

        {report.fingerprint_changed && (
          <div className="mt-4" data-testid="fingerprint-changed">
            <h2 className="text-xs uppercase tracking-wider text-muted">
              Model configuration changed since the baseline
            </h2>
            <dl className="mt-2 space-y-1">
              {changed.map((field) => (
                <div key={field} className="numeric text-sm">
                  <dt className="inline text-muted">{field}: </dt>
                  <dd className="inline text-ink">
                    {String(report.baseline_fingerprint[field])} →{" "}
                    {String(report.current_fingerprint[field])}
                  </dd>
                </div>
              ))}
            </dl>
            <p className="mt-2 max-w-prose text-xs text-muted">
              A changed fingerprint is usually the reason for the comparison, but
              it is sometimes the explanation for a regression that is really a
              different model.
            </p>
          </div>
        )}

        <div className="mt-5">
          <Caveat>
            A metric counts as drifted only when the interval for the difference
            excludes zero. Every rate moves between runs; reporting raw deltas
            would make this fire constantly and be ignored. A unit whose outcome
            worsened is reported as drift regardless of significance, because a
            zero-tolerance control can flip from pass to fail on a single
            exception.
          </Caveat>
        </div>
      </header>

      {report.units.map((unit) => (
        <section
          key={`${unit.probe_id}-${unit.unit}`}
          className="border-t border-rule py-6"
          data-testid="drift-unit"
        >
          <header className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
            <div>
              <h2 className="text-base font-medium text-ink">{unit.probe_id}</h2>
              <p className="numeric text-xs text-muted">{unit.unit}</p>
            </div>
            <div className="flex items-center gap-2 text-xs">
              <OutcomeTag outcome={unit.baseline_outcome as Outcome} />
              <span className="text-muted">→</span>
              <OutcomeTag outcome={unit.current_outcome as Outcome} />
              {unit.outcome_worsened && (
                <span className="text-fail" data-testid="outcome-worsened">
                  outcome worsened
                </span>
              )}
            </div>
          </header>

          {unit.config_changed && (
            <p className="mb-3 text-sm text-fail" data-testid="config-changed">
              This unit was reconfigured since the baseline; the two runs did not
              perform the same procedure.
            </p>
          )}

          <div className="space-y-6">
            {unit.metrics.map((metric) => (
              <div key={metric.metric}>
                <h3 className="mb-2 text-sm text-ink">
                  {metricLabel(metric.metric)}
                  <span className="ml-2 text-xs text-muted">{metric.verdict}</span>
                </h3>
                <SharedScale
                  rows={[
                    { label: "baseline", measurement: metric.baseline },
                    { label: "current", measurement: metric.current },
                  ]}
                />
                <DifferenceBar metric={metric} />
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
