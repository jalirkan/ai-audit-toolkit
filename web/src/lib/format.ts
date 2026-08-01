/**
 * The one place a Measurement becomes text.
 *
 * `Measurement.render()` in `core/evidence.py` is the only sanctioned way to
 * put a rate in front of a reader on the Python side, and this is its
 * counterpart. Everything that displays a measured quantity goes through
 * here, which is how the no-bare-rates rule (D-004, D-008, D-030) survives
 * contact with a second language.
 *
 * The atom is `0.091 (95% CI [0.025, 0.278], 2/22)`. There is no view,
 * tooltip, summary, or export in which the rate appears without its interval
 * and its n. If you find yourself writing `${m.value}` anywhere else in this
 * codebase, that is the bug this module exists to prevent.
 *
 * Rates render as decimals, not percentages. The engine does the same, and it
 * means the only percent sign in any output comes from the confidence level
 * itself -- which is what lets a scan for bare percentages be meaningful.
 */

import type { Measurement } from "../api/schema";

export const KIND_PROPORTION = "proportion";
export const KIND_MEAN = "mean";
export const KIND_COUNT = "count";

/** `n === 0` means *not tested*. It is never zero, and never a rate. */
export function isInformative(m: Measurement): boolean {
  return m.n > 0;
}

export function hasInterval(m: Measurement): boolean {
  return m.ci_low !== null && m.ci_high !== null;
}

function fixed(x: number, places = 3): string {
  return x.toFixed(places);
}

/**
 * The full, honest rendering of a measurement.
 *
 * Mirrors `Measurement.render()`: the value, its interval, and either the
 * numerator over n or n alone. A count has no interval and is rendered as a
 * tally rather than being drawn as one.
 */
export function renderMeasurement(m: Measurement, places = 3): string {
  if (!isInformative(m)) {
    return `not tested (n=0) [${m.name}]`;
  }

  if (m.kind === KIND_COUNT) {
    const value = Math.round(m.value);
    return value === m.n ? `${value}` : `${value} of ${m.n}`;
  }

  let body = fixed(m.value, places);
  if (m.ci_low !== null && m.ci_high !== null) {
    const pct = Math.round((m.confidence ?? 0.95) * 100);
    body += ` (${pct}% CI [${fixed(m.ci_low, places)}, ${fixed(
      m.ci_high,
      places,
    )}]`;
    body += m.successes !== null ? `, ${m.successes}/${m.n}` : `, n=${m.n}`;
    body += ")";
  } else {
    body += ` (n=${m.n})`;
  }
  return body;
}

/**
 * The sample statement on its own, for places that show it beside a mark.
 *
 * Still never a bare rate: this is the denominator half of the atom, used
 * where the value is carried by the interval drawing rather than by text.
 */
export function renderSample(m: Measurement): string {
  if (!isInformative(m)) return "not tested (n=0)";
  return m.successes !== null ? `${m.successes} of ${m.n}` : `n = ${m.n}`;
}

export function renderInterval(m: Measurement, places = 3): string {
  if (m.ci_low === null || m.ci_high === null) return "no interval";
  const pct = Math.round((m.confidence ?? 0.95) * 100);
  return `${pct}% CI [${fixed(m.ci_low, places)}, ${fixed(m.ci_high, places)}]`;
}

/** Human label for a metric name: `unsupported_answer_rate` reads badly. */
export function metricLabel(name: string): string {
  return name.replace(/_/g, " ");
}

const OUTCOME_LABELS: Record<string, string> = {
  pass: "Pass",
  fail: "Fail",
  inconclusive: "Inconclusive",
  error: "Error",
};

export function outcomeLabel(outcome: string): string {
  return OUTCOME_LABELS[outcome] ?? outcome;
}

/**
 * What each outcome means, in the engine's own terms.
 *
 * Written deliberately rather than generated, and kept short enough to sit
 * next to the mark it explains. "Inconclusive" gets the longest gloss because
 * it is the one a reader is most likely to mistake for a soft failure.
 */
export const OUTCOME_MEANING: Record<string, string> = {
  pass: "The whole interval sits on the acceptable side of the criterion.",
  fail: "The whole interval sits on the unacceptable side of the criterion.",
  inconclusive:
    "The interval straddles the criterion, so this sample cannot settle the question. Not a finding, and not a pass.",
  error: "The procedure did not complete, so it produced no finding either way.",
};

export function formatTimestamp(iso: string): string {
  if (!iso) return "";
  // The engine writes UTC with a Z suffix and means it. Rendering it in local
  // time would make two evidence records taken minutes apart look like they
  // came from different days depending on who opened the file.
  const cleaned = iso.replace(/\.(\d{3})\d*Z$/, ".$1Z");
  const d = new Date(cleaned);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`
  );
}

export function shortHash(hash: string): string {
  const body = hash.includes(":") ? hash.split(":")[1] ?? hash : hash;
  return body.slice(0, 12);
}
