/**
 * Outcome marks and small shared primitives.
 *
 * Three outcomes plus error, each with its own fill AND shape, so they are
 * told apart on a monochrome printout and by a reader with colour vision
 * deficiency. Hue is the last of the three signals, never the only one.
 *
 * Inconclusive is hollow and hatched on purpose. Placing it on a good-to-bad
 * ramp between pass and fail would say the sample found something halfway
 * bad, when what it found was nothing conclusive at all (D-011).
 */

import type { ReactNode } from "react";
import type { Outcome, OutcomeCounts } from "../api/schema";
import { OUTCOME_MEANING, outcomeLabel } from "../lib/format";

const GLYPH: Record<Outcome, string> = {
  // Shape carries the distinction before colour does.
  pass: "■",
  fail: "▲",
  inconclusive: "◻",
  error: "—",
};

const TEXT: Record<Outcome, string> = {
  pass: "text-pass",
  fail: "text-fail",
  inconclusive: "text-inconclusive",
  error: "text-error",
};

const BORDER: Record<Outcome, string> = {
  pass: "border-pass/40 bg-pass/8",
  fail: "border-fail/45 bg-fail/8",
  // Unfilled: absence of a finding, not a lesser one.
  inconclusive: "border-inconclusive/50 border-dashed bg-transparent",
  error: "border-error/45 border-dotted bg-transparent",
};

export function OutcomeTag({
  outcome,
  showMeaning = false,
}: {
  outcome: Outcome;
  showMeaning?: boolean;
}) {
  return (
    <span
      data-testid="outcome-tag"
      data-outcome={outcome}
      title={showMeaning ? OUTCOME_MEANING[outcome] : undefined}
      className={`inline-flex items-center gap-1.5 border px-2 py-0.5 text-xs tracking-wide uppercase ${BORDER[outcome]} ${TEXT[outcome]}`}
    >
      <span aria-hidden="true">{GLYPH[outcome]}</span>
      {outcomeLabel(outcome)}
    </span>
  );
}

/**
 * The rollup, as three distinct marks rather than one status.
 *
 * A battery has no composite score (D-016); its rollup is a distribution.
 * Rendering the counts side by side keeps that visible -- one control failing
 * badly among several passing is exactly the case an averaged figure hides.
 */
export function OutcomeCountsRow({ counts }: { counts: OutcomeCounts }) {
  const order: Outcome[] = ["fail", "error", "inconclusive", "pass"];
  const present = order.filter((o) => (counts[o] ?? 0) > 0);
  return (
    <span className="inline-flex items-center gap-3" data-testid="outcome-counts">
      {present.map((outcome) => (
        <span
          key={outcome}
          className={`inline-flex items-baseline gap-1 text-xs ${TEXT[outcome]}`}
          data-outcome={outcome}
        >
          <span aria-hidden="true">{GLYPH[outcome]}</span>
          <span className="numeric">{counts[outcome]}</span>
          <span className="text-muted">{outcomeLabel(outcome).toLowerCase()}</span>
        </span>
      ))}
    </span>
  );
}

export function Section({
  title,
  subtitle,
  children,
  printBreak = false,
}: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
  printBreak?: boolean;
}) {
  return (
    <section
      className="mb-10"
      data-print={printBreak ? "break-before" : undefined}
    >
      <h2 className="text-sm font-semibold uppercase tracking-[0.12em] text-muted">
        {title}
      </h2>
      {subtitle && <p className="mt-1 max-w-prose text-sm text-ink-soft">{subtitle}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

/**
 * A statement the reader must not be able to miss.
 *
 * Used for the things the engine insists on saying: what verification does not
 * prove, that a mapping is not compliance, that a scale is zoomed. Rendered as
 * text on the page, never as a tooltip -- a caveat behind a hover is a caveat
 * that does not print.
 */
export function Caveat({ children }: { children: ReactNode }) {
  return (
    <p
      data-testid="caveat"
      // Marked as a denial so the no-invented-aggregate scan can tell a
      // disclaimer from a claim. "No composite score is computed" contains the
      // word "composite"; a scan blind to polarity would flag the sentence
      // that exists to prevent the thing it is looking for (cf. D-040).
      data-role="denial"
      className="max-w-prose border-l-2 border-rule-strong pl-3 text-sm text-ink-soft"
    >
      {children}
    </p>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wider text-muted">{label}</dt>
      <dd className="mt-0.5 text-sm text-ink">{children}</dd>
    </div>
  );
}
