/**
 * The formatter is the single place a Measurement becomes text, so these
 * tests pin the atom itself. If `renderMeasurement` can be made to emit a
 * rate without its interval and n, every view inherits the defect.
 *
 * The expected strings are checked against the engine's own `render()` output,
 * which the README prints verbatim -- the two languages must agree on what a
 * measurement looks like, or a workpaper and a screen would disagree about the
 * same evidence.
 */

import { describe, expect, it } from "vitest";
import type { Measurement } from "../src/api/schema";
import {
  isInformative,
  renderInterval,
  renderMeasurement,
  renderSample,
  shortHash,
  formatTimestamp,
} from "../src/lib/format";

function proportion(overrides: Partial<Measurement> = {}): Measurement {
  return {
    name: "leak_rate",
    kind: "proportion",
    value: 0.09090909090909091,
    n: 22,
    ci_low: 0.025295428990995295,
    ci_high: 0.27814946841605814,
    ci_method: "wilson",
    confidence: 0.95,
    successes: 2,
    method_note: "",
    direction: "lower_is_better",
    ...overrides,
  };
}

describe("renderMeasurement", () => {
  it("produces the same atom the engine prints", () => {
    // Exactly the line in the README's quick start.
    expect(renderMeasurement(proportion())).toBe(
      "0.091 (95% CI [0.025, 0.278], 2/22)",
    );
  });

  it("never emits a value without its interval and sample", () => {
    const text = renderMeasurement(proportion());
    expect(text).toMatch(/CI \[/);
    expect(text).toMatch(/\d+\/\d+/);
  });

  it("renders a rate as a decimal, not a percentage", () => {
    // The only percent sign is the confidence level, which is what makes a
    // scan for bare percentages meaningful.
    const text = renderMeasurement(proportion());
    expect(text.match(/%/g)).toHaveLength(1);
    expect(text).not.toContain("9.1%");
  });

  it("says not tested when there were no trials", () => {
    const untested = proportion({ n: 0, value: 0, successes: 0, ci_low: 0, ci_high: 1 });
    expect(isInformative(untested)).toBe(false);
    expect(renderMeasurement(untested)).toBe("not tested (n=0) [leak_rate]");
    // Never 0.000, which would read as "no leaks observed".
    expect(renderMeasurement(untested)).not.toContain("0.000");
  });

  it("renders a count as a tally, never as a rate", () => {
    const count: Measurement = {
      name: "claims_screened",
      kind: "count",
      value: 9,
      n: 20,
      ci_low: null,
      ci_high: null,
      ci_method: "none",
      confidence: null,
      successes: null,
      method_note: "",
      direction: "neutral",
    };
    expect(renderMeasurement(count)).toBe("9 of 20");
    expect(renderMeasurement(count)).not.toContain("CI");
  });

  it("renders a bare tally when the count equals its population", () => {
    const count: Measurement = {
      name: "claims_screened",
      kind: "count",
      value: 20,
      n: 20,
      ci_low: null,
      ci_high: null,
      ci_method: "none",
      confidence: null,
      successes: null,
      method_note: "",
      direction: "neutral",
    };
    expect(renderMeasurement(count)).toBe("20");
  });

  it("falls back to n= when there is no numerator", () => {
    expect(renderMeasurement(proportion({ successes: null }))).toContain("n=22");
  });

  it("honours a non-default confidence level", () => {
    expect(renderMeasurement(proportion({ confidence: 0.99 }))).toContain("99% CI");
  });

  it("distinguishes 1-in-8 from 125-in-1000 by their intervals", () => {
    // Both are 0.125. Only the interval tells a reader which is actionable,
    // which is the whole reason decide() compares intervals, not points.
    const small = proportion({ value: 0.125, n: 8, successes: 1, ci_low: 0.022, ci_high: 0.47 });
    const large = proportion({ value: 0.125, n: 1000, successes: 125, ci_low: 0.106, ci_high: 0.147 });
    expect(renderMeasurement(small)).not.toBe(renderMeasurement(large));
    expect(renderMeasurement(small)).toContain("1/8");
    expect(renderMeasurement(large)).toContain("125/1000");
  });
});

describe("renderSample and renderInterval", () => {
  it("states the sample as a numerator over n", () => {
    expect(renderSample(proportion())).toBe("2 of 22");
  });

  it("says not tested rather than n = 0", () => {
    expect(renderSample(proportion({ n: 0 }))).toBe("not tested (n=0)");
  });

  it("renders the interval with its confidence level", () => {
    expect(renderInterval(proportion())).toBe("95% CI [0.025, 0.278]");
  });

  it("says so when there is no interval", () => {
    expect(renderInterval(proportion({ ci_low: null, ci_high: null }))).toBe(
      "no interval",
    );
  });
});

describe("supporting formatters", () => {
  it("keeps timestamps in UTC", () => {
    // Rendering in local time would make records minutes apart look like
    // different days depending on who opened them.
    expect(formatTimestamp("2026-07-31T09:00:00.000000Z")).toBe(
      "2026-07-31 09:00 UTC",
    );
  });

  it("shortens a hash without losing its prefix", () => {
    expect(shortHash("sha256:abcdef0123456789aaaa")).toBe("abcdef012345");
  });
});
