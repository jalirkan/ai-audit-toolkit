/**
 * One run: every unit it tested, with its interval against its criterion.
 *
 * This is the screen the interval component justifies itself on. Each unit
 * gets its decision rationale in full -- the engine writes `notes` to be read,
 * so it is rendered as a sentence rather than truncated into a cell -- plus
 * the criterion that was applied and the count of exceptions a reviewer would
 * inspect.
 *
 * Every measurement the record carries is shown, not only the deciding one.
 * The citation probe reports a claim-level rate alongside the answer-level
 * rate its conclusion rests on (D-014), and hiding the second would leave a
 * reader unable to see why the finer-grained figure was not used.
 */

import type { BatteryResult, Evidence } from "../api/schema";
import { decisionMeasurement, unitOf } from "../api/schema";
import { IntervalRow } from "../design/Interval";
import { Caveat, Field, OutcomeCountsRow, OutcomeTag } from "../design/Outcome";
import { formatTimestamp, metricLabel, shortHash } from "../lib/format";

function UnitCard({
  evidence,
  onOpenTrials,
}: {
  evidence: Evidence;
  onOpenTrials?: () => void;
}) {
  const decisive = decisionMeasurement(evidence);
  const threshold =
    typeof evidence.config.decision_threshold === "number"
      ? evidence.config.decision_threshold
      : null;
  const rule =
    typeof evidence.config.decision_rule === "string"
      ? evidence.config.decision_rule
      : undefined;
  const exceptions = evidence.trials.filter((t) => t.passed === false);

  return (
    <article
      className="border-t border-rule py-6"
      data-print="keep-together"
      data-testid="unit-card"
      data-probe={evidence.probe_id}
    >
      <header className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h3 className="text-base font-medium text-ink">{evidence.probe_id}</h3>
          {unitOf(evidence) && (
            <p className="numeric text-xs text-muted">{unitOf(evidence)}</p>
          )}
        </div>
        <OutcomeTag outcome={evidence.outcome} showMeaning />
      </header>

      {evidence.measurements.length === 0 ? (
        <p className="text-sm text-muted">
          No measurements were produced; the procedure did not complete.
        </p>
      ) : (
        <div className="space-y-5">
          {evidence.measurements.map((m) => (
            <IntervalRow
              key={m.name}
              measurement={m}
              outcome={evidence.outcome}
              // Only the deciding measurement was compared to the criterion.
              // Drawing the threshold on the others would imply they were
              // judged against it, and they were not.
              threshold={decisive && m.name === decisive.name ? threshold : null}
              decisionRule={rule}
              label={metricLabel(m.name)}
            />
          ))}
        </div>
      )}

      {evidence.notes && (
        <div className="mt-5">
          <h4 className="text-xs uppercase tracking-wider text-muted">
            Basis of conclusion
          </h4>
          {/* The engine writes this to be read. Rendered whole. */}
          <p className="mt-1 max-w-prose text-sm text-ink-soft">{evidence.notes}</p>
        </div>
      )}

      <dl className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Field label="Criterion">
          {threshold === null ? (
            "—"
          ) : rule === "zero-tolerance-attribute" ? (
            <span data-role="criterion">no exceptions permitted</span>
          ) : (
            <span className="numeric" data-role="criterion">
              {threshold.toFixed(3)}
            </span>
          )}
        </Field>
        <Field label="Decision rule">{rule ?? "—"}</Field>
        <Field label="Items examined">
          <span className="numeric">{evidence.trials.length}</span>
        </Field>
        <Field label="Exceptions">
          <span className="numeric">{exceptions.length}</span>
        </Field>
      </dl>

      {onOpenTrials && (
        <button
          className="mt-4 text-xs uppercase tracking-wider text-accent hover:underline"
          onClick={onOpenTrials}
          data-print="hide"
          data-testid="open-trials"
        >
          Examine the {evidence.trials.length} model call(s) →
        </button>
      )}

      {decisive && evidence.measurements.length > 1 && (
        <p className="mt-3 text-xs text-muted">
          The conclusion rests on {metricLabel(decisive.name)}; the other
          figures are reported but were not the criterion applied.
        </p>
      )}

      {decisive?.method_note && (
        <p className="mt-3 max-w-prose text-xs text-muted">
          {decisive.method_note}
        </p>
      )}
    </article>
  );
}

export function RunDetail({
  run,
  onBack,
  onOpenWorkpaper,
  onOpenTrials,
}: {
  run: BatteryResult;
  onBack: () => void;
  onOpenWorkpaper?: () => void;
  onOpenTrials?: (unit: number) => void;
}) {
  return (
    <div>
      <button
        className="mb-4 text-xs uppercase tracking-wider text-muted hover:text-ink"
        onClick={onBack}
        data-print="hide"
      >
        ← All runs
      </button>

      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">{run.battery}</h1>
        {run.description && (
          <p className="mt-1 max-w-prose text-sm text-ink-soft">{run.description}</p>
        )}

        <dl className="mt-5 grid grid-cols-2 gap-5 sm:grid-cols-4">
          <Field label="Run">
            <span className="numeric">{run.run_id}</span>
          </Field>
          <Field label="Endpoint">
            {run.fingerprint.adapter}:{run.fingerprint.model}
          </Field>
          <Field label="Started">
            <span className="numeric">{formatTimestamp(run.started_at)}</span>
          </Field>
          <Field label="Units tested">
            <span className="numeric">{run.evidence.length}</span>
          </Field>
        </dl>

        <div className="mt-5 flex flex-wrap items-center gap-4">
          <OutcomeCountsRow counts={run.outcome_counts} />
          <OutcomeTag outcome={run.outcome} showMeaning />
        </div>

        {onOpenWorkpaper && (
          <button
            className="mt-5 border border-rule px-3 py-1.5 text-xs uppercase tracking-wider text-ink-soft hover:border-rule-strong hover:text-ink"
            onClick={onOpenWorkpaper}
            data-print="hide"
            data-testid="open-workpaper"
          >
            Open workpaper
          </button>
        )}

        <div className="mt-5">
          <Caveat>
            The run-level outcome is chosen by precedence — fail, then error,
            then inconclusive, then pass — not by averaging. No composite score
            is computed, because averaging a leak rate with an agreement rate
            would hide the case a reader most needs to see.
          </Caveat>
        </div>
      </header>

      <section>
        <h2 className="text-sm font-semibold uppercase tracking-[0.12em] text-muted">
          Units tested
        </h2>
        {run.evidence.map((evidence, i) => (
          <UnitCard
            key={`${evidence.probe_id}-${unitOf(evidence)}-${i}`}
            evidence={evidence}
            onOpenTrials={onOpenTrials ? () => onOpenTrials(i) : undefined}
          />
        ))}
      </section>

      <footer className="mt-10 border-t border-rule pt-4 text-xs text-muted">
        <span className="numeric">
          Model fingerprint {shortHash(run.fingerprint.system_prompt_hash ?? "")}
          {run.fingerprint.system_prompt_hash ? " · " : ""}
          {run.fingerprint.adapter}:{run.fingerprint.model}
        </span>
      </footer>
    </div>
  );
}
