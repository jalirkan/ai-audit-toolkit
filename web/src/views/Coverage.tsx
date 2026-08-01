/**
 * Framework coverage, gaps included.
 *
 * The gaps are the point (D-026, D-027). A coverage view that showed only what
 * was tested would flatter the engagement, so controls with no evidence render
 * as present-and-empty rather than being filtered out — and they are counted
 * in the same summary as everything else.
 *
 * There are no green ticks against control identifiers. A mapping asserts that
 * a procedure produced evidence *relevant* to a control, never that the
 * control is satisfied, and the disclaimer sits on the page rather than in a
 * tooltip because a caveat behind a hover is a caveat that does not print.
 *
 * `tested-with-exceptions` is its own status, distinct from both "covered" and
 * "no evidence": the control was tested and it failed, which is a different
 * fact from either.
 */

import { useState } from "react";
import type { CoveragePayload, ControlCoveragePayload } from "../api/comparison";
import { Caveat } from "../design/Outcome";

const STATUS_LABEL: Record<string, string> = {
  "no-evidence": "No evidence",
  "evidence-present": "Evidence present",
  "tested-pass": "Tested — no exceptions",
  "tested-inconclusive": "Tested — inconclusive",
  "tested-error": "Tested — procedure errored",
  "tested-with-exceptions": "Tested — exceptions noted",
};

/** Glyph first, colour second, so the page survives greyscale. */
const STATUS_GLYPH: Record<string, string> = {
  "no-evidence": "○",
  "evidence-present": "◍",
  "tested-pass": "■",
  "tested-inconclusive": "◻",
  "tested-error": "—",
  "tested-with-exceptions": "▲",
};

const STATUS_CLASS: Record<string, string> = {
  "no-evidence": "text-muted",
  "evidence-present": "text-ink-soft",
  "tested-pass": "text-pass",
  "tested-inconclusive": "text-inconclusive",
  "tested-error": "text-error",
  "tested-with-exceptions": "text-fail",
};

function ControlRow({ control }: { control: ControlCoveragePayload }) {
  const [open, setOpen] = useState(false);
  const sources = [...control.probe_ids, ...control.capabilities];
  return (
    <tr
      className="border-b border-rule align-top"
      data-testid="control-row"
      data-status={control.status}
    >
      <td className="numeric py-3 pr-4 text-ink">{control.control_id}</td>
      <td className="py-3 pr-4 text-ink-soft">
        {control.summary}
        {control.references.length > 0 && (
          <>
            <button
              className="ml-2 text-[10px] uppercase tracking-wider text-accent hover:underline"
              onClick={() => setOpen((v) => !v)}
              data-print="hide"
            >
              {open ? "hide rationale" : "why this maps"}
            </button>
            {open && (
              <ul
                className="mt-2 list-disc space-y-1 pl-4 text-xs text-muted"
                data-testid="mapping-rationale"
              >
                {control.references.map((r, i) => (
                  <li key={i} className="max-w-prose">
                    {r.rationale}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </td>
      <td className={`py-3 pr-4 ${STATUS_CLASS[control.status] ?? ""}`}>
        <span aria-hidden="true" className="mr-1.5">
          {STATUS_GLYPH[control.status]}
        </span>
        {STATUS_LABEL[control.status] ?? control.status}
      </td>
      <td className="py-3 text-xs text-muted">
        {sources.length > 0 ? sources.join(", ") : "—"}
      </td>
    </tr>
  );
}

export function Coverage({ coverage }: { coverage: CoveragePayload }) {
  const [hideGaps, setHideGaps] = useState(false);
  const totalGaps = coverage.frameworks.reduce(
    (n, f) => n + f.controls.filter((c) => c.status === "no-evidence").length,
    0,
  );

  return (
    <div>
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">
          Framework coverage
        </h1>
        <div className="mt-3">
          {/* D-027 verbatim from the API, on the page, not in a tooltip. */}
          <Caveat>{coverage.disclaimer}</Caveat>
        </div>
        <p className="mt-4 numeric text-sm text-ink-soft">
          {totalGaps} catalogued control(s) have no evidence from this run.
        </p>
      </header>

      <div className="mb-6" data-print="hide">
        <label className="text-xs uppercase tracking-wider text-muted">
          <input
            type="checkbox"
            className="mr-2"
            checked={hideGaps}
            onChange={(e) => setHideGaps(e.target.checked)}
          />
          Collapse controls with no evidence
        </label>
        {hideGaps && (
          <p className="mt-2 max-w-prose text-xs text-fail" data-testid="gaps-hidden-warning">
            {totalGaps} control(s) are hidden. A coverage report that shows only
            what was tested flatters the engagement — this view is for scanning,
            not for reporting.
          </p>
        )}
      </div>

      {coverage.frameworks.map((framework) => {
        const controls = hideGaps
          ? framework.controls.filter((c) => c.status !== "no-evidence")
          : framework.controls;
        return (
          <section key={framework.framework.id} className="mb-10" data-print="keep-together">
            <h2 className="text-sm font-semibold uppercase tracking-[0.12em] text-muted">
              {framework.framework.name}
            </h2>
            <p className="mt-1 text-xs text-muted">
              {framework.framework.publication}
              {framework.framework.partial && " · partial catalog"}
              {framework.framework.ids_verified &&
                ` · identifiers checked ${framework.framework.ids_verified}`}
            </p>
            {framework.framework.note && (
              <p className="mt-2 max-w-prose text-xs text-muted">
                {framework.framework.note}
              </p>
            )}

            <table className="mt-4 w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-rule-strong text-left text-xs uppercase tracking-wider text-muted">
                  <th className="py-2 pr-4 font-medium">Control</th>
                  <th className="py-2 pr-4 font-medium">Summary</th>
                  <th className="py-2 pr-4 font-medium">Status</th>
                  <th className="py-2 font-medium">Evidenced by</th>
                </tr>
              </thead>
              <tbody>
                {controls.map((control) => (
                  <ControlRow key={control.control_id} control={control} />
                ))}
              </tbody>
            </table>
          </section>
        );
      })}

      {coverage.inactive_sources.length > 0 && (
        <section className="border-t border-rule pt-4">
          <h2 className="text-xs uppercase tracking-wider text-muted">
            Mapped procedures not active in this run
          </h2>
          <p className="mt-2 max-w-prose text-sm text-ink-soft">
            These would close further gaps if run: {coverage.inactive_sources.join(", ")}.
          </p>
        </section>
      )}
    </div>
  );
}
