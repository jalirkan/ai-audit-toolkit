/**
 * The three tests that carry the thesis.
 *
 * These are not ordinary coverage. They assert the properties that make this
 * a reviewer's instrument rather than another dashboard, and each one has a
 * companion proving the check can actually fail -- a guard that cannot fail
 * proves nothing, which is the discipline `test_report.py` already applies to
 * the Markdown and HTML renderers.
 *
 * Fixtures are real engine output, captured by
 * `web/tests/fixtures/generate.py`. The scripted demo run carries a fail, a
 * pass, and an inconclusive unit in one payload (D-034), so every treatment
 * has a real subject rather than a hand-written one that can drift.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import runA from "./fixtures/run-d51a4ffee83b0707.json";
import runB from "./fixtures/run-6061be9acf3a4779.json";
import runsIndexFixture from "./fixtures/runs-index.json";

import { IntervalMark, IntervalRow } from "../src/design/Interval";
import { OutcomeTag } from "../src/design/Outcome";
import { RunDetail } from "../src/views/RunDetail";
import { RunsIndex } from "../src/views/RunsIndex";
import { parseBatteryResult } from "../src/api/schema";
import type { Measurement, RunSummary } from "../src/api/schema";

// Identified by the endpoint they tested, not by filename. Run ids are
// derived, so a regenerated fixture set renames these files -- binding the
// mixed-outcome run to a filename would silently swap the subject of every
// test below, which is exactly what happened once while writing them.
const parsed = [runA, runB].map(parseBatteryResult);
const scripted = parsed.find((r) => r.fingerprint.model === "demo-vendor-assistant")!;
const bare = parsed.find((r) => r.fingerprint.model === "bare-mock")!;
const runs = runsIndexFixture as unknown as RunSummary[];

/** A percentage, as the Python scan defines one. */
const PERCENT = /\d+(?:\.\d+)?\s*%/;
/** The engine's rate format: three decimal places. */
const RATE = /\b\d+\.\d{3}\b/;
const SAMPLE = /(\d+\s*\/\s*\d+|\bn\s*=\s*\d+|\d+ of \d+)/;

/**
 * The nearest ancestor that behaves like a line of text.
 *
 * The DOM has no lines, so the Python scan's "the line it appears on" becomes
 * "the block this text sits in". Walking up to a block-level container is the
 * closest honest translation.
 */
function enclosingBlock(node: Node): HTMLElement | null {
  let current: HTMLElement | null =
    node.nodeType === Node.TEXT_NODE
      ? node.parentElement
      : (node as HTMLElement);
  while (current) {
    const display = current.getAttribute("data-block") ?? "";
    if (
      display === "true" ||
      ["DIV", "P", "TD", "LI", "SECTION", "ARTICLE", "DD", "TR"].includes(
        current.tagName,
      )
    ) {
      return current;
    }
    current = current.parentElement;
  }
  return null;
}

/**
 * Every rate shown to a reader must carry its interval and its sample size.
 *
 * Exempt: anything explicitly marked `data-role="criterion"` or
 * `data-role="axis"`. A criterion is a constant the auditor configured and an
 * axis tick is a scale label; neither is an estimate, so neither has an
 * interval to report. The exemption is an attribute rather than a wording
 * match so it must be declared in code and cannot be triggered by accident.
 */
function assertNoBareRates(container: HTMLElement, label: string): void {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  let node: Node | null = walker.nextNode();
  while (node) {
    const text = node.textContent ?? "";
    const parent = node.parentElement;
    const exempt = parent?.closest("[data-role='criterion'],[data-role='axis']");

    if (!exempt && (PERCENT.test(text) || RATE.test(text))) {
      const block = enclosingBlock(node);
      const context = block?.textContent ?? text;
      expect(
        context,
        `${label}: a rate appears without an interval: ${text.trim()}`,
      ).toMatch(/CI/);
      expect(
        context,
        `${label}: a rate appears without a sample size: ${text.trim()}`,
      ).toMatch(SAMPLE);
    }
    node = walker.nextNode();
  }
}

describe("1. no bare rates", () => {
  it("holds across a run carrying fail, pass and inconclusive units", () => {
    // The subject is chosen deliberately: this one payload contains all three.
    const counts = scripted.outcome_counts;
    expect(counts.fail).toBe(1);
    expect(counts.pass).toBe(1);
    expect(counts.inconclusive).toBe(1);

    const { container } = render(<RunDetail run={scripted} onBack={() => {}} />);
    assertNoBareRates(container, "run detail (mixed outcomes)");
  });

  it("holds on a run whose every unit failed", () => {
    const { container } = render(<RunDetail run={bare} onBack={() => {}} />);
    assertNoBareRates(container, "run detail (all failing)");
  });

  it("holds on the runs index", () => {
    const { container } = render(<RunsIndex runs={runs} onSelect={() => {}} />);
    assertNoBareRates(container, "runs index");
  });

  it("holds when a measurement has no trials at all", () => {
    const untested: Measurement = {
      name: "leak_rate",
      kind: "proportion",
      value: 0,
      n: 0,
      ci_low: 0,
      ci_high: 1,
      ci_method: "wilson",
      confidence: 0.95,
      successes: 0,
      method_note: "",
      direction: "lower_is_better",
    };
    const { container } = render(
      <IntervalRow measurement={untested} outcome="inconclusive" threshold={0.1} />,
    );
    expect(container.textContent).toContain("not tested");
    // n=0 is not a rate of zero, and must never be drawn as one.
    expect(container.textContent).not.toMatch(/\b0\.000\b/);
    assertNoBareRates(container, "untested measurement");
  });

  it("the scan actually fails on a planted bare rate", () => {
    // Guards the guard.
    const { container } = render(<p>Leak rate was 12%.</p>);
    expect(() => assertNoBareRates(container, "planted")).toThrow();
  });

  it("the scan actually fails on a rate with an interval but no sample size", () => {
    const { container } = render(<p>0.091 (95% CI [0.025, 0.278])</p>);
    expect(() => assertNoBareRates(container, "planted")).toThrow();
  });

  it("the scan passes the honest atom", () => {
    const { container } = render(<p>0.091 (95% CI [0.025, 0.278], 2/22)</p>);
    expect(() => assertNoBareRates(container, "atom")).not.toThrow();
  });
});

describe("2. no invented aggregate", () => {
  /**
   * The engine refuses to compute a composite (D-016, D-036). The UI must not
   * invent one -- no gauge, no "87% healthy", no per-endpoint total.
   */
  const FORBIDDEN = [
    "composite",
    "overall score",
    "health score",
    "healthy",
    "grade",
    "ranking",
    "rank ",
    "winner",
    "best endpoint",
    "average score",
    "total score",
  ];

  /**
   * Text with the deliberate denials removed.
   *
   * A scan blind to polarity flags "no composite score is computed" as a
   * composite score -- the same failure D-040 found in the citation screen,
   * where a claim and its exact negation matched a source equally well. The
   * denials are marked in code with `data-role="denial"`, and a separate test
   * below asserts they are still present, so this exemption cannot be used to
   * hide a real aggregate.
   */
  function assertedText(container: HTMLElement): string {
    const clone = container.cloneNode(true) as HTMLElement;
    clone.querySelectorAll("[data-role='denial']").forEach((n) => n.remove());
    return (clone.textContent ?? "").toLowerCase();
  }

  it.each([
    ["run detail", () => render(<RunDetail run={scripted} onBack={() => {}} />)],
    ["runs index", () => render(<RunsIndex runs={runs} onSelect={() => {}} />)],
  ])("%s asserts no aggregate figure", (label, mount) => {
    const { container } = mount();
    const text = assertedText(container);
    for (const term of FORBIDDEN) {
      expect(text, `${label} contains "${term}"`).not.toContain(term);
    }
  });

  it.each([
    ["run detail", () => render(<RunDetail run={scripted} onBack={() => {}} />)],
    ["runs index", () => render(<RunsIndex runs={runs} onSelect={() => {}} />)],
  ])("%s renders no summary percentage", (_label, mount) => {
    const { container } = mount();
    // The shape an invented aggregate would actually take.
    expect(assertedText(container)).not.toMatch(
      /\d+(\.\d+)?\s*%\s*(healthy|overall|assurance|compliant)/,
    );
  });

  it("the run detail says outright that no composite is computed", () => {
    render(<RunDetail run={scripted} onBack={() => {}} />);
    const caveats = screen.getAllByTestId("caveat");
    const text = caveats.map((c) => c.textContent).join(" ");
    expect(text).toMatch(/no composite score/i);
    expect(text).toMatch(/precedence/i);
  });

  it("the rollup is shown as counts per outcome, not one number", () => {
    render(<RunsIndex runs={runs} onSelect={() => {}} />);
    const rows = screen.getAllByTestId("outcome-counts");
    expect(rows.length).toBeGreaterThan(0);
    // Three distinct marks for the mixed run, not a single status.
    const mixed = rows.find((r) => r.querySelectorAll("[data-outcome]").length === 3);
    expect(mixed).toBeDefined();
  });

  it("the denials are actually on the page, so the exemption cannot hide one", () => {
    // If the disclaimers were deleted, the exemption above would quietly stop
    // exempting anything -- and this test fails first, saying why.
    const detail = render(<RunDetail run={scripted} onBack={() => {}} />);
    expect(
      detail.container.querySelectorAll("[data-role='denial']").length,
    ).toBeGreaterThan(0);
    const index = render(<RunsIndex runs={runs} onSelect={() => {}} />);
    expect(
      index.container.querySelectorAll("[data-role='denial']").length,
    ).toBeGreaterThan(0);
  });

  it("the check would catch an invented aggregate", () => {
    // Guards the guard: an unmarked claim is still caught.
    const { container } = render(<p>Overall score: 87% healthy</p>);
    const text = assertedText(container);
    expect(FORBIDDEN.some((term) => text.includes(term))).toBe(true);
    expect(text).toMatch(/\d+(\.\d+)?\s*%\s*(healthy|overall|assurance|compliant)/);
  });

  it("the exemption does not swallow an aggregate placed outside a denial", () => {
    const { container } = render(
      <div>
        <p data-role="denial">No composite score is computed.</p>
        <p>Composite: 0.87</p>
      </div>,
    );
    expect(assertedText(container)).toContain("composite");
  });
});

describe("3. inconclusive is distinct, and not on a good-to-bad ramp", () => {
  function marks(outcome: "pass" | "fail" | "inconclusive") {
    const measurement: Measurement = {
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
    };
    const { container } = render(
      <IntervalMark measurement={measurement} outcome={outcome} threshold={0.2} />,
    );
    const bar = container.querySelector("[data-testid='interval-bar']")!;
    const point = container.querySelector("[data-testid='point-estimate']")!;
    return { bar: bar.className, point: point.className };
  }

  it("the inconclusive bar is not the fail bar", () => {
    expect(marks("inconclusive").bar).not.toBe(marks("fail").bar);
  });

  it("the inconclusive bar is not the pass bar", () => {
    expect(marks("inconclusive").bar).not.toBe(marks("pass").bar);
  });

  it("inconclusive reads as absence: unfilled, hatched, dashed", () => {
    const { bar } = marks("inconclusive");
    expect(bar).toContain("hatch-absence");
    expect(bar).toContain("border-dashed");
    expect(bar).toContain("bg-transparent");
  });

  it("pass and fail read as present findings: solid fill", () => {
    expect(marks("pass").bar).toContain("bg-pass");
    expect(marks("fail").bar).toContain("bg-fail");
    expect(marks("pass").bar).not.toContain("hatch-absence");
    expect(marks("fail").bar).not.toContain("hatch-absence");
  });

  it("is distinguishable without colour, by glyph", () => {
    // Shape carries the distinction first, so a greyscale print still reads.
    const glyphs = (["pass", "fail", "inconclusive", "error"] as const).map((o) => {
      const { container } = render(<OutcomeTag outcome={o} />);
      const tag = within(container).getByTestId("outcome-tag");
      return tag.querySelector("[aria-hidden='true']")?.textContent ?? "";
    });
    expect(new Set(glyphs).size).toBe(4);
  });

  it("is distinguishable without colour, by the point-estimate treatment", () => {
    expect(marks("inconclusive").point).not.toBe(marks("fail").point);
    expect(marks("inconclusive").point).toContain("border-2");
  });

  it("carries a written meaning that separates it from failure", () => {
    render(<OutcomeTag outcome="inconclusive" showMeaning />);
    const tag = screen.getByTestId("outcome-tag");
    expect(tag.getAttribute("title")).toMatch(/cannot settle the question/i);
    expect(tag.getAttribute("title")).toMatch(/not a finding, and not a pass/i);
  });

  it("the check would notice if inconclusive were styled as fail", () => {
    // If someone collapsed the two treatments, the comparisons above fail.
    expect(marks("fail").bar).toBe(marks("fail").bar);
    expect(marks("inconclusive").bar === marks("fail").bar).toBe(false);
  });
});
