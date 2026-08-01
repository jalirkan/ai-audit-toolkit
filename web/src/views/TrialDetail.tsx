/**
 * The individual model calls behind a finding.
 *
 * This is the bottom of the drill-down and the reason the rest of the app can
 * claim to be a reviewer's instrument: a reviewer who cannot reach the exact
 * prompt, system prompt, and response cannot challenge the conclusion drawn
 * from them.
 *
 * Two rules govern the layout. **Nothing is truncated.** A prompt or a
 * response cut to fit a table cell is evidence the reader cannot assess, so
 * text is rendered whole and wrapped, in a monospace face where whitespace
 * carries meaning. And **the label that fired is shown next to the exception**
 * -- knowing a trial failed is not the same as knowing the screen matched a
 * reversed canary in it.
 */

import { useMemo, useState } from "react";
import type { Evidence, Trial } from "../api/schema";
import { unitOf } from "../api/schema";
import { OutcomeTag } from "../design/Outcome";

function CopyButton({ text, what }: { text: string; what: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      data-print="hide"
      className="text-[10px] uppercase tracking-wider text-muted hover:text-ink"
      onClick={() => {
        void navigator.clipboard?.writeText(text);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1200);
      }}
      aria-label={`Copy ${what}`}
    >
      {copied ? "copied" : "copy"}
    </button>
  );
}

function TextBlock({
  label,
  text,
  what,
}: {
  label: string;
  text: string;
  what: string;
}) {
  if (!text) return null;
  return (
    <div className="mt-3">
      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-xs uppercase tracking-wider text-muted">{label}</span>
        <CopyButton text={text} what={what} />
      </div>
      {/* Whole, wrapped, monospace. Never truncated: an unreadable exhibit is
          not evidence. */}
      <pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-words border border-rule bg-raised p-3 text-xs leading-relaxed text-ink">
        {text}
      </pre>
    </div>
  );
}

function labelEntries(labels: Record<string, unknown>): [string, string][] {
  return Object.entries(labels)
    .filter(([, v]) => v !== "" && v !== null && v !== undefined && v !== false)
    .map(([k, v]) => [k, String(v)]);
}

function TrialCard({ trial, index }: { trial: Trial; index: number }) {
  const isException = trial.passed === false;
  const fired = labelEntries(trial.labels);
  return (
    <article
      data-testid="trial-card"
      data-exception={isException ? "true" : "false"}
      data-print="keep-together"
      className={`border-l-2 py-4 pl-4 ${
        isException ? "border-fail" : "border-rule"
      }`}
    >
      <header className="flex flex-wrap items-baseline gap-3">
        <span className="numeric text-xs text-muted">#{trial.index ?? index}</span>
        {isException ? (
          <span className="text-xs uppercase tracking-wider text-fail">
            Exception
          </span>
        ) : trial.passed === true ? (
          <span className="text-xs uppercase tracking-wider text-muted">
            No exception
          </span>
        ) : (
          <span className="text-xs uppercase tracking-wider text-muted">
            Not judged individually
          </span>
        )}
        {fired.length > 0 && (
          <span className="flex flex-wrap gap-2" data-testid="trial-labels">
            {fired.map(([k, v]) => (
              <span
                key={k}
                className="border border-rule px-1.5 py-0.5 text-[10px] numeric text-ink-soft"
              >
                {k}: {v}
              </span>
            ))}
          </span>
        )}
        <span className="ml-auto numeric text-[10px] text-muted">
          {trial.latency_ms.toFixed(1)} ms
          {trial.usage
            ? ` · ${trial.usage.prompt_tokens ?? "?"}+${
                trial.usage.completion_tokens ?? "?"
              } tokens`
            : " · usage not reported"}
        </span>
      </header>

      <TextBlock label="System prompt" text={trial.system ?? ""} what="system prompt" />
      <TextBlock label="Prompt" text={trial.prompt} what="prompt" />
      <TextBlock label="Response" text={trial.response_text} what="response" />
    </article>
  );
}

export function TrialDetail({
  evidence,
  onBack,
}: {
  evidence: Evidence;
  onBack: () => void;
}) {
  const [only, setOnly] = useState<"all" | "exceptions">("all");
  const exceptions = useMemo(
    () => evidence.trials.filter((t) => t.passed === false),
    [evidence.trials],
  );
  const shown = only === "exceptions" ? exceptions : evidence.trials;

  return (
    <div>
      <button
        className="mb-4 text-xs uppercase tracking-wider text-muted hover:text-ink"
        onClick={onBack}
        data-print="hide"
      >
        ← Back to run
      </button>

      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">
          {evidence.probe_id}
        </h1>
        {unitOf(evidence) && (
          <p className="numeric text-sm text-muted">{unitOf(evidence)}</p>
        )}
        <div className="mt-3">
          <OutcomeTag outcome={evidence.outcome} showMeaning />
        </div>
        <p className="mt-4 max-w-prose text-sm text-ink-soft">
          Every model call made by this procedure, in order. The prompt, the
          system prompt in force, and the response are shown whole — nothing is
          truncated, because an exhibit a reviewer cannot read is not evidence.
        </p>
      </header>

      <div className="mb-4 flex items-center gap-4" data-print="hide">
        <label className="text-xs uppercase tracking-wider text-muted">
          Show{" "}
          <select
            className="ml-1 border border-rule bg-raised px-2 py-1 text-sm text-ink"
            value={only}
            onChange={(e) => setOnly(e.target.value as "all" | "exceptions")}
            aria-label="Filter trials"
          >
            <option value="all">all items examined</option>
            <option value="exceptions">exceptions only</option>
          </select>
        </label>
        <span className="numeric text-xs text-muted">
          {exceptions.length} exception(s) in {evidence.trials.length} item(s)
          examined
        </span>
      </div>

      <div className="space-y-4">
        {shown.map((trial, i) => (
          <TrialCard key={trial.index ?? i} trial={trial} index={i} />
        ))}
      </div>

      {shown.length === 0 && (
        <p className="text-sm text-muted">
          No exceptions were noted in this unit.
        </p>
      )}
    </div>
  );
}
