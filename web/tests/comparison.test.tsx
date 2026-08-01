/**
 * Coverage, drift, and comparison.
 *
 * The test that matters most here is that "not distinguished" is drawn rather
 * than merely stated: two intervals that overlap must appear on ONE axis, both
 * positioned against the same domain. Two marks on separately-zoomed axes are
 * not a comparison, they are two pictures near each other — and a reader who
 * cannot see the overlap has been given the tool's assertion instead of the
 * evidence for it.
 */

import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import comparisonFixture from "./fixtures/comparison.json";
import driftFixture from "./fixtures/drift.json";
import driftCleanFixture from "./fixtures/drift-clean.json";
import coverageFixture from "./fixtures/coverage-d51a4ffee83b0707.json";

import type {
  ComparisonPayload,
  CoveragePayload,
  DriftPayload,
} from "../src/api/comparison";
import { Comparison } from "../src/views/Comparison";
import { Coverage } from "../src/views/Coverage";
import { Drift } from "../src/views/Drift";
import { SharedScale } from "../src/design/SharedScale";
import type { Measurement } from "../src/api/schema";

const matrix = comparisonFixture as unknown as ComparisonPayload;
const drift = driftFixture as unknown as DriftPayload;
const driftClean = driftCleanFixture as unknown as DriftPayload;
const coverage = coverageFixture as unknown as CoveragePayload;

function proportion(over: Partial<Measurement> = {}): Measurement {
  return {
    name: "leak_rate",
    kind: "proportion",
    value: 0.1,
    n: 20,
    ci_low: 0.03,
    ci_high: 0.3,
    ci_method: "wilson",
    confidence: 0.95,
    successes: 2,
    method_note: "",
    direction: "lower_is_better",
    ...over,
  };
}

describe("the shared scale", () => {
  it("positions every row against one domain", () => {
    // The property that makes it a comparison at all.
    const { container } = render(
      <SharedScale
        rows={[
          { label: "a", measurement: proportion({ ci_low: 0, ci_high: 0.2 }) },
          { label: "b", measurement: proportion({ ci_low: 0.1, ci_high: 0.4 }) },
        ]}
      />,
    );
    const bars = Array.from(
      container.querySelectorAll("[data-testid='scale-bar']"),
    ) as HTMLElement[];
    expect(bars).toHaveLength(2);
    // Same domain => the wider interval must render wider.
    const width = (el: HTMLElement) => parseFloat(el.style.width);
    expect(width(bars[1]!)).toBeGreaterThan(width(bars[0]!));
    // And the second must start further right.
    expect(parseFloat(bars[1]!.style.left)).toBeGreaterThan(
      parseFloat(bars[0]!.style.left),
    );
  });

  it("marks the group as overlapping when the engine says so", () => {
    const { container } = render(
      <SharedScale rows={[{ label: "a", measurement: proportion() }]} overlapping />,
    );
    expect(
      container.querySelector("[data-testid='shared-scale']")?.getAttribute(
        "data-overlapping",
      ),
    ).toBe("true");
  });

  it("renders a not-tested row without drawing a bar", () => {
    const { container } = render(
      <SharedScale
        rows={[{ label: "a", measurement: proportion({ n: 0 }) }]}
      />,
    );
    expect(container.querySelector("[data-testid='scale-bar']")).toBeNull();
    expect(container.textContent).toContain("not tested (n=0)");
  });

  it("carries the figure with every mark", () => {
    const { container } = render(
      <SharedScale rows={[{ label: "a", measurement: proportion() }]} />,
    );
    expect(container.textContent).toContain("0.100 (95% CI [0.030, 0.300], 2/20)");
  });
});

describe("comparison", () => {
  it("draws the undistinguished metric's intervals on one shared axis", () => {
    // This is F3's whole point. leak_rate overlaps in the fixture.
    render(<Comparison matrix={matrix} />);
    const section = screen.getByTestId("undistinguished-section");
    const scales = within(section).getAllByTestId("shared-scale");
    expect(scales.length).toBeGreaterThan(0);
    const rows = within(scales[0]!).getAllByTestId("scale-row");
    // Both endpoints on the same axis, not one chart each.
    expect(rows).toHaveLength(2);
    expect(scales[0]!.getAttribute("data-overlapping")).toBe("true");
  });

  it("names the undistinguished metric the engine flagged", () => {
    const flagged = matrix.metric_rows.filter((r) => r.all_overlap);
    expect(flagged.length).toBeGreaterThan(0);
    render(<Comparison matrix={matrix} />);
    const section = screen.getByTestId("undistinguished-section");
    for (const row of flagged) {
      expect(section.textContent).toContain(row.metric.replace(/_/g, " "));
    }
  });

  it("says the run has not shown the endpoints to differ", () => {
    render(<Comparison matrix={matrix} />);
    expect(screen.getByTestId("undistinguished-section").textContent).toMatch(
      /has not shown these endpoints to differ/i,
    );
  });

  it("never uses the engine's overlap flag it did not compute itself", () => {
    // The client reads all_overlap; it must not recompute the rule. If the
    // fixture says overlapping, the view must say overlapping.
    const inverted: ComparisonPayload = {
      ...matrix,
      metric_rows: matrix.metric_rows.map((r) => ({ ...r, all_overlap: false })),
    };
    render(<Comparison matrix={inverted} />);
    expect(screen.getByTestId("undistinguished-section").textContent).toMatch(
      /Every metric separated the endpoints/i,
    );
  });

  it("renders no ranking, winner, or endpoint score", () => {
    // Denials are stripped before scanning, the same convention honesty.test
    // uses: "no overall ranking is produced" contains "ranking", and a check
    // blind to polarity flags the sentence written to prevent the thing it is
    // looking for. Marked in code with data-role="denial", never by wording.
    const { container } = render(<Comparison matrix={matrix} />);
    const clone = container.cloneNode(true) as HTMLElement;
    clone.querySelectorAll("[data-role='denial']").forEach((n) => n.remove());
    const text = (clone.textContent ?? "").toLowerCase();
    for (const term of ["winner", "rank", "1st", "best endpoint", "score:"]) {
      expect(text, `comparison asserts "${term}"`).not.toContain(term);
    }
  });

  it("the denial it relies on is actually on the page", () => {
    const { container } = render(<Comparison matrix={matrix} />);
    const denials = container.querySelectorAll("[data-role='denial']");
    expect(denials.length).toBeGreaterThan(0);
    expect(
      Array.from(denials)
        .map((d) => d.textContent)
        .join(" "),
    ).toMatch(/no overall ranking/i);
  });

  it("reports token coverage rather than implying a complete total", () => {
    // D-037: k of n calls reported usage; absence is not zero.
    const { container } = render(<Comparison matrix={matrix} />);
    expect(container.textContent).toMatch(/\d+ of \d+ reported usage/);
  });
});

describe("drift", () => {
  it("draws baseline and current on one axis", () => {
    const { container } = render(<Drift report={drift} />);
    const scales = container.querySelectorAll("[data-testid='shared-scale']");
    expect(scales.length).toBeGreaterThan(0);
    const rows = within(scales[0] as HTMLElement).getAllByTestId("scale-row");
    expect(rows.map((r) => r.getAttribute("data-label"))).toEqual([
      "baseline",
      "current",
    ]);
  });

  it("marks zero on the difference interval", () => {
    const { container } = render(<Drift report={drift} />);
    expect(container.querySelectorAll("[data-testid='zero-mark']").length).toBeGreaterThan(0);
  });

  it("distinguishes an interval that excludes zero from one that does not", () => {
    const { container } = render(<Drift report={drift} />);
    const bars = Array.from(
      container.querySelectorAll("[data-testid='difference-bar']"),
    );
    const flags = new Set(bars.map((b) => b.getAttribute("data-excludes-zero")));
    // The fixture has both kinds; if it ever has only one, this still holds.
    expect(bars.length).toBeGreaterThan(0);
    expect([...flags].every((f) => f === "true" || f === "false")).toBe(true);
  });

  it("states that including zero means no change was shown", () => {
    const { container } = render(<Drift report={driftClean} />);
    expect(container.textContent).toMatch(/has not shown the rate to have changed/i);
  });

  it("reports the bootstrap seed, so the analysis can be re-run", () => {
    // D-022: an auditor re-running this must get the same numbers.
    const { container } = render(<Drift report={drift} />);
    expect(container.textContent).toMatch(/seed \d+/);
  });

  it("names the fingerprint fields that changed", () => {
    expect(drift.fingerprint_changed).toBe(true);
    render(<Drift report={drift} />);
    const block = screen.getByTestId("fingerprint-changed");
    expect(block.textContent).toContain("model");
    expect(block.textContent).toMatch(/demo-vendor-assistant|bare-mock/);
  });

  it("flags a worsened outcome even where the metric did not move significantly", () => {
    // D-023: 0/22 -> 1/22 flips pass to fail under zero tolerance.
    const worsened = drift.units.filter((u) => u.outcome_worsened);
    if (worsened.length === 0) return;
    render(<Drift report={drift} />);
    expect(screen.getAllByTestId("outcome-worsened").length).toBe(worsened.length);
  });
});

describe("coverage", () => {
  it("renders controls with no evidence rather than filtering them out", () => {
    render(<Coverage coverage={coverage} />);
    const gaps = screen
      .getAllByTestId("control-row")
      .filter((r) => r.getAttribute("data-status") === "no-evidence");
    expect(gaps.length).toBeGreaterThan(0);
  });

  it("puts the mapping disclaimer on the page, not in a tooltip", () => {
    render(<Coverage coverage={coverage} />);
    const caveats = screen.getAllByTestId("caveat");
    expect(caveats.map((c) => c.textContent).join(" ")).toMatch(
      /never that the control is satisfied/i,
    );
  });

  it("shows tested-with-exceptions as its own status", () => {
    // Distinct from both "covered" and "no evidence" (D-027).
    const statuses = new Set(
      coverage.frameworks.flatMap((f) => f.controls.map((c) => c.status)),
    );
    if (!statuses.has("tested-with-exceptions")) return;
    render(<Coverage coverage={coverage} />);
    const rows = screen
      .getAllByTestId("control-row")
      .filter((r) => r.getAttribute("data-status") === "tested-with-exceptions");
    expect(rows.length).toBeGreaterThan(0);
    expect(rows[0]!.textContent).toMatch(/exceptions noted/i);
  });

  it("warns when gaps are collapsed out of view", () => {
    render(<Coverage coverage={coverage} />);
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByTestId("gaps-hidden-warning").textContent).toMatch(
      /flatters the engagement/i,
    );
  });

  it("shows the mapping rationale on demand", () => {
    // D-027: an unargued mapping cannot be defended in review.
    render(<Coverage coverage={coverage} />);
    const buttons = screen.getAllByText(/why this maps/i);
    expect(buttons.length).toBeGreaterThan(0);
    fireEvent.click(buttons[0]!);
    expect(screen.getAllByTestId("mapping-rationale").length).toBeGreaterThan(0);
  });

  it("marks every catalogue as partial", () => {
    // D-026: a control's absence is not a statement about it.
    const { container } = render(<Coverage coverage={coverage} />);
    const partials = coverage.frameworks.filter((f) => f.framework.partial);
    expect(partials.length).toBe(coverage.frameworks.length);
    expect(container.textContent).toContain("partial catalog");
  });

  it("renders no green tick against a control identifier", () => {
    const { container } = render(<Coverage coverage={coverage} />);
    expect(container.textContent).not.toContain("✓");
    expect(container.textContent).not.toContain("✔");
  });
});
