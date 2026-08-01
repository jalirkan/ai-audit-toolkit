/**
 * The interval on a scale, with the threshold marked.
 *
 * This is the component the whole design rests on. Whether the interval clears
 * the criterion, sits entirely past it, or straddles it *is* the pass / fail /
 * inconclusive decision -- so one mark encodes the engine's `decide()` rule
 * visually, and a reader can check the conclusion against the drawing without
 * being told what to think.
 *
 * Four things it refuses to do:
 *
 * 1. **It does not draw a count as an interval.** A count has no uncertainty
 *    to plot (`ci_method: "none"`), so it renders as a tally. Drawing a bar
 *    for it would invent precision.
 *
 * 2. **It does not draw `n === 0` as zero.** No trials means not tested, and
 *    the component says so rather than plotting a point at the origin.
 *
 * 3. **It does not assume higher is better.** `direction` comes off the
 *    measurement (D-021) and decides which side of the threshold is shaded as
 *    unacceptable. The same +0.10 is bad for a leak rate and good for an
 *    agreement rate, and the drawing has to know which.
 *
 * 4. **It does not put inconclusive on a ramp between pass and fail.** The
 *    inconclusive treatment is hatched and unfilled -- absence of evidence --
 *    while pass and fail are solid, present findings.
 *
 * Zero-tolerance controls (D-012) get a distinct threshold marker, because a
 * criterion of exactly 0.0 is not a tolerance the interval is compared
 * against; it is attribute sampling, where any exception fails and the
 * interval is shown to say what n bought.
 */

import type { Measurement, Outcome } from "../api/schema";
import {
  isInformative,
  renderInterval,
  renderMeasurement,
  renderSample,
} from "../lib/format";

const RULE_ZERO_TOLERANCE = "zero-tolerance-attribute";

export interface IntervalScale {
  min: number;
  max: number;
}

export interface IntervalMarkProps {
  measurement: Measurement;
  outcome: Outcome;
  threshold?: number | null;
  decisionRule?: string;
  /** Force a shared domain. F3 passes one so two endpoints share an axis. */
  scale?: IntervalScale;
  /** Row height in px. Dense in tables, taller where it is the subject. */
  height?: number;
  label?: string;
}

/**
 * Pick a domain that makes the mark readable.
 *
 * A leak rate of 0.09 against a 0–1 axis is a smear at the left edge, so the
 * default zooms to the data and the threshold, and the axis always prints its
 * endpoints so nobody mistakes a zoomed scale for a full one.
 */
export function defaultScale(
  measurement: Measurement,
  threshold?: number | null,
): IntervalScale {
  const points = [measurement.value];
  if (measurement.ci_low !== null) points.push(measurement.ci_low);
  if (measurement.ci_high !== null) points.push(measurement.ci_high);
  if (threshold !== null && threshold !== undefined) points.push(threshold);

  const high = Math.max(...points, 0);
  const proportion = measurement.kind === "proportion";
  // Leave headroom so a mark never touches the axis end.
  let max = high * 1.25;
  if (proportion) max = Math.min(1, Math.max(max, 0.1));
  else max = Math.max(max, high + 1);
  return { min: 0, max: max || 1 };
}

function position(value: number, scale: IntervalScale): number {
  const span = scale.max - scale.min;
  if (span <= 0) return 0;
  return Math.min(100, Math.max(0, ((value - scale.min) / span) * 100));
}

const BAR_CLASS: Record<Outcome, string> = {
  // Findings: solid, present.
  pass: "bg-pass",
  fail: "bg-fail",
  // Absence of a finding: unfilled, hatched, dashed border.
  inconclusive: "hatch-absence text-inconclusive border border-dashed border-inconclusive bg-transparent",
  error: "bg-transparent border border-dotted border-error",
};

const POINT_CLASS: Record<Outcome, string> = {
  pass: "bg-pass",
  fail: "bg-fail",
  inconclusive: "bg-paper border-2 border-inconclusive",
  error: "bg-paper border-2 border-error",
};

export function IntervalMark({
  measurement,
  outcome,
  threshold,
  decisionRule,
  scale,
  height = 14,
  label,
}: IntervalMarkProps) {
  // Not tested is not zero. Say so and draw nothing.
  if (!isInformative(measurement)) {
    return (
      <div className="text-muted numeric" data-testid="interval-not-tested">
        not tested (n=0)
      </div>
    );
  }

  // A count carries no interval and must not be drawn as one.
  if (measurement.kind === "count" || measurement.ci_low === null || measurement.ci_high === null) {
    return (
      <div className="numeric text-ink-soft" data-testid="interval-count">
        {renderMeasurement(measurement)}
      </div>
    );
  }

  const domain = scale ?? defaultScale(measurement, threshold);
  const low = position(measurement.ci_low, domain);
  const high = position(measurement.ci_high, domain);
  const point = position(measurement.value, domain);
  const lowerIsBetter = measurement.direction !== "higher_is_better";
  const zeroTolerance = decisionRule === RULE_ZERO_TOLERANCE;
  const hasThreshold = threshold !== null && threshold !== undefined;
  const thresholdAt = hasThreshold ? position(threshold, domain) : null;

  const description =
    `${label ?? measurement.name}: ${renderMeasurement(measurement)}` +
    (hasThreshold
      ? `, criterion ${threshold.toFixed(3)} (${
          lowerIsBetter ? "lower is better" : "higher is better"
        })`
      : "");

  return (
    <div className="w-full" data-testid="interval-mark" data-outcome={outcome}>
      {/* The plot. role=img with a full text description, because the mark
          carries the conclusion and a screen reader must get it too. */}
      <div
        role="img"
        aria-label={description}
        className="relative w-full rounded-[1px] bg-rule/40"
        style={{ height }}
      >
        {/* The unacceptable region, so `direction` is visible and not implied. */}
        {thresholdAt !== null && !zeroTolerance && (
          <div
            aria-hidden="true"
            data-testid="unacceptable-region"
            className="absolute inset-y-0 bg-fail/8"
            style={
              lowerIsBetter
                ? { left: `${thresholdAt}%`, right: 0 }
                : { left: 0, width: `${thresholdAt}%` }
            }
          />
        )}

        {/* The interval itself. */}
        <div
          data-testid="interval-bar"
          className={`absolute top-1/2 -translate-y-1/2 rounded-[1px] ${BAR_CLASS[outcome]}`}
          style={{
            left: `${low}%`,
            width: `${Math.max(high - low, 0.6)}%`,
            height: Math.max(height - 6, 4),
          }}
        />

        {/* The point estimate, shaped by outcome so it survives greyscale. */}
        <div
          aria-hidden="true"
          data-testid="point-estimate"
          className={`absolute top-1/2 -translate-y-1/2 -translate-x-1/2 rounded-full ${POINT_CLASS[outcome]}`}
          style={{ left: `${point}%`, width: 6, height: 6 }}
        />

        {/* The criterion. The one place the accent colour is spent. */}
        {thresholdAt !== null && (
          <div
            aria-hidden="true"
            data-testid="threshold-mark"
            data-zero-tolerance={zeroTolerance ? "true" : "false"}
            className={`absolute inset-y-0 w-px bg-accent ${
              zeroTolerance ? "outline outline-1 outline-accent" : ""
            }`}
            style={{ left: `${thresholdAt}%` }}
          />
        )}
      </div>

      {/* The axis prints its endpoints so a zoomed scale cannot be mistaken
          for a full one. */}
      <div className="mt-1 flex justify-between text-[10px] text-muted numeric">
        <span data-role="axis">{domain.min.toFixed(2)}</span>
        {hasThreshold && (
          // Marked as a criterion, not a measurement. A threshold is a
          // constant the auditor configured; it carries no sampling
          // uncertainty, so it has no interval to show. The no-bare-rates
          // scan exempts it on this attribute rather than on nearby wording,
          // so the exemption has to be declared deliberately in code.
          <span className="text-accent" data-role="criterion">
            {zeroTolerance
              ? "criterion: no exceptions permitted"
              : `criterion ${threshold.toFixed(3)} · ${
                  lowerIsBetter ? "lower is better" : "higher is better"
                }`}
          </span>
        )}
        <span data-role="axis">{domain.max.toFixed(2)}</span>
      </div>
    </div>
  );
}

/**
 * The mark with its figure written out beside it.
 *
 * Used wherever the interval is the subject rather than a table cell. The text
 * always comes from `renderMeasurement`, so the rate can never appear here
 * without its interval and n.
 */
export function IntervalRow({
  measurement,
  outcome,
  threshold,
  decisionRule,
  scale,
  label,
}: IntervalMarkProps) {
  return (
    <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,22rem)] sm:items-center">
      <div>
        <div className="text-sm text-ink">{label ?? measurement.name}</div>
        <div className="numeric text-ink-soft" data-testid="measurement-text">
          {renderMeasurement(measurement)}
        </div>
        {!isInformative(measurement) ? null : (
          <div className="text-xs text-muted">
            {renderSample(measurement)} · {renderInterval(measurement)}
          </div>
        )}
      </div>
      <IntervalMark
        measurement={measurement}
        outcome={outcome}
        threshold={threshold}
        decisionRule={decisionRule}
        scale={scale}
        height={18}
        label={label}
      />
    </div>
  );
}
