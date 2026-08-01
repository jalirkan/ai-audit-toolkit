/**
 * The interval component encodes `decide()` visually, so these tests check the
 * drawing agrees with the rule rather than merely that it renders.
 *
 * The properties that matter: a count is never drawn as an interval, n=0 is
 * never drawn as zero, direction decides which side is shaded unacceptable,
 * and a zero-tolerance criterion is marked as the different rule it is.
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { IntervalMark, defaultScale } from "../src/design/Interval";
import type { Measurement } from "../src/api/schema";

function measurement(overrides: Partial<Measurement> = {}): Measurement {
  return {
    name: "leak_rate",
    kind: "proportion",
    value: 0.091,
    n: 22,
    ci_low: 0.025,
    ci_high: 0.278,
    ci_method: "wilson",
    confidence: 0.95,
    successes: 2,
    method_note: "",
    direction: "lower_is_better",
    ...overrides,
  };
}

describe("what the component refuses to draw", () => {
  it("does not draw a count as an interval", () => {
    const count = measurement({
      kind: "count",
      name: "claims_screened",
      value: 9,
      n: 20,
      ci_low: null,
      ci_high: null,
      ci_method: "none",
      confidence: null,
      successes: null,
    });
    const { queryByTestId, getByTestId } = render(
      <IntervalMark measurement={count} outcome="pass" />,
    );
    expect(queryByTestId("interval-bar")).toBeNull();
    expect(getByTestId("interval-count").textContent).toBe("9 of 20");
  });

  it("does not draw n=0 as a point at the origin", () => {
    const { queryByTestId, getByTestId } = render(
      <IntervalMark measurement={measurement({ n: 0 })} outcome="inconclusive" />,
    );
    expect(queryByTestId("interval-bar")).toBeNull();
    expect(getByTestId("interval-not-tested").textContent).toContain("not tested");
  });
});

describe("direction is consumed, not assumed", () => {
  it("shades above the threshold when lower is better", () => {
    const { getByTestId } = render(
      <IntervalMark
        measurement={measurement({ direction: "lower_is_better" })}
        outcome="fail"
        threshold={0.2}
      />,
    );
    const region = getByTestId("unacceptable-region") as HTMLElement;
    // Unacceptable region runs from the threshold to the right-hand end.
    expect(region.style.left).not.toBe("");
    expect(region.style.right).toBe("0px");
  });

  it("shades below the threshold when higher is better", () => {
    const { getByTestId } = render(
      <IntervalMark
        measurement={measurement({
          name: "expected_answer_rate",
          direction: "higher_is_better",
          value: 0.9,
          ci_low: 0.8,
          ci_high: 0.97,
        })}
        outcome="pass"
        threshold={0.8}
      />,
    );
    const region = getByTestId("unacceptable-region") as HTMLElement;
    expect(region.style.left).toBe("0px");
    expect(region.style.width).not.toBe("");
  });

  it("states the direction in the axis label", () => {
    const { container } = render(
      <IntervalMark measurement={measurement()} outcome="fail" threshold={0.2} />,
    );
    expect(container.textContent).toContain("lower is better");
  });
});

describe("zero-tolerance criteria are marked as a different rule", () => {
  it("labels the criterion as admitting no exceptions", () => {
    const { container, getByTestId } = render(
      <IntervalMark
        measurement={measurement({ value: 0, successes: 0, ci_low: 0, ci_high: 0.149 })}
        outcome="pass"
        threshold={0}
        decisionRule="zero-tolerance-attribute"
      />,
    );
    expect(container.textContent).toContain("no exceptions permitted");
    expect(getByTestId("threshold-mark").getAttribute("data-zero-tolerance")).toBe(
      "true",
    );
  });

  it("does not shade an unacceptable region for a zero-tolerance control", () => {
    // Under attribute sampling the interval is not compared to the threshold,
    // so shading a region would misdescribe the rule that was applied.
    const { queryByTestId } = render(
      <IntervalMark
        measurement={measurement({ value: 0, successes: 0, ci_low: 0, ci_high: 0.149 })}
        outcome="pass"
        threshold={0}
        decisionRule="zero-tolerance-attribute"
      />,
    );
    expect(queryByTestId("unacceptable-region")).toBeNull();
  });
});

describe("the scale", () => {
  it("zooms so a small rate is legible", () => {
    const scale = defaultScale(measurement(), 0.2);
    expect(scale.max).toBeLessThan(1);
    expect(scale.max).toBeGreaterThan(0.278);
  });

  it("never exceeds 1 for a proportion", () => {
    const scale = defaultScale(
      measurement({ value: 1, ci_low: 0.839, ci_high: 1 }),
      0.8,
    );
    expect(scale.max).toBe(1);
  });

  it("includes the threshold even when the data sits far below it", () => {
    const scale = defaultScale(
      measurement({ value: 0.01, ci_low: 0, ci_high: 0.05 }),
      0.5,
    );
    expect(scale.max).toBeGreaterThanOrEqual(0.5);
  });

  it("prints its endpoints so a zoomed axis cannot be mistaken for 0-1", () => {
    const { container } = render(
      <IntervalMark measurement={measurement()} outcome="fail" threshold={0.2} />,
    );
    const axis = container.querySelectorAll("[data-role='axis']");
    expect(axis).toHaveLength(2);
    expect(axis[0]!.textContent).toBe("0.00");
  });

  it("honours a shared scale when one is supplied", () => {
    // F3 draws two endpoints on one axis; the component must not re-zoom.
    const { getByTestId } = render(
      <IntervalMark
        measurement={measurement()}
        outcome="fail"
        scale={{ min: 0, max: 1 }}
      />,
    );
    const bar = getByTestId("interval-bar") as HTMLElement;
    expect(bar.style.left).toBe("2.5%");
  });
});

describe("accessibility", () => {
  it("describes the whole conclusion for a screen reader", () => {
    const { getByRole } = render(
      <IntervalMark measurement={measurement()} outcome="fail" threshold={0.2} />,
    );
    const label = getByRole("img").getAttribute("aria-label") ?? "";
    expect(label).toContain("0.091");
    expect(label).toContain("95% CI");
    expect(label).toContain("2/22");
    expect(label).toContain("criterion 0.200");
    expect(label).toContain("lower is better");
  });
});
