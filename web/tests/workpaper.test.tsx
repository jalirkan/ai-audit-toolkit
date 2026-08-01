/**
 * The workpaper and the trial drill-down.
 *
 * The workpaper's job is to be handed to a reviewer, so the tests check the
 * things that would make it useless in that role: a missing limitations block,
 * a truncated exhibit, a conclusion with no evidence hash beside it, or a
 * print layout that drops the caveats along with the navigation.
 */

import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import runA from "./fixtures/run-d51a4ffee83b0707.json";
import runB from "./fixtures/run-6061be9acf3a4779.json";
import wpA from "./fixtures/workpaper-d51a4ffee83b0707.json";
import wpB from "./fixtures/workpaper-6061be9acf3a4779.json";

import { parseBatteryResult } from "../src/api/schema";
import { parseDocument } from "../src/api/document";
import { Workpaper } from "../src/views/Workpaper";
import { TrialDetail } from "../src/views/TrialDetail";
import { RunDetail } from "../src/views/RunDetail";

const runs = [runA, runB].map(parseBatteryResult);
const scripted = runs.find((r) => r.fingerprint.model === "demo-vendor-assistant")!;
const papers = [wpA, wpB].map(parseDocument);
const scriptedPaper = papers.find((d) =>
  JSON.stringify(d).includes("demo-vendor-assistant"),
)!;

function allText(container: HTMLElement): string {
  return container.textContent ?? "";
}

describe("the workpaper is faithful to the engine's document", () => {
  it("renders every workpaper section the model carries", () => {
    const { container } = render(
      <Workpaper document={scriptedPaper} onBack={() => {}} />,
    );
    const text = allText(container);
    for (const heading of [
      "Procedure performed",
      "Population and examination",
      "Result",
      "Conclusion",
      "Limitations of this procedure",
    ]) {
      expect(text).toContain(heading);
    }
  });

  it("shows the evidence hash that ties a finding to the journal", () => {
    const { container } = render(
      <Workpaper document={scriptedPaper} onBack={() => {}} />,
    );
    expect(allText(container)).toMatch(/sha256:[0-9a-f]{16}/);
  });

  it("renders the limitations where the result is, not as a footnote", () => {
    // D-015: the cost of a lexical screen is stated next to the finding.
    render(<Workpaper document={scriptedPaper} onBack={() => {}} />);
    const limitations = screen.getAllByTestId("limitation");
    expect(limitations.length).toBeGreaterThan(0);
    expect(limitations.map((l) => l.textContent).join(" ")).toMatch(/lower bound/i);
  });

  it("describes the population as a complete examination, not a sample", () => {
    // D-031: "sample of 22" would imply a sampling frame that does not exist.
    const text = allText(
      render(<Workpaper document={scriptedPaper} onBack={() => {}} />).container,
    );
    expect(text.toLowerCase()).toContain("complete");
  });

  it("carries no rate without an interval", () => {
    const text = allText(
      render(<Workpaper document={scriptedPaper} onBack={() => {}} />).container,
    );
    // Every percent sign in the document belongs to a confidence level.
    const percents = text.match(/\d+(?:\.\d+)?\s*%/g) ?? [];
    for (const hit of percents) {
      expect(hit).toMatch(/9\d\s*%/);
    }
  });

  it("refuses a document schema it does not understand", () => {
    expect(() =>
      parseDocument({ ...scriptedPaper, schema_version: 99 }),
    ).toThrow(/newer than this build/);
  });
});

describe("the print layout", () => {
  it("marks navigation and controls as hidden when printed", () => {
    const { container } = render(
      <Workpaper document={scriptedPaper} onBack={() => {}} />,
    );
    const hidden = container.querySelectorAll("[data-print='hide']");
    expect(hidden.length).toBeGreaterThan(0);
    // The print button and the back link are chrome, not evidence.
    expect(
      Array.from(hidden).some((n) => /print/i.test(n.textContent ?? "")),
    ).toBe(true);
  });

  it("keeps each workpaper unit together across page breaks", () => {
    const { container } = render(
      <Workpaper document={scriptedPaper} onBack={() => {}} />,
    );
    const units = container.querySelectorAll("[data-testid='workpaper-unit']");
    expect(units.length).toBeGreaterThan(0);
    units.forEach((unit) => {
      expect(unit.getAttribute("data-print")).toBe("keep-together");
    });
  });

  it("does not mark the limitations as print-hidden", () => {
    // The caveats are the part a reviewer most needs on paper.
    const { container } = render(
      <Workpaper document={scriptedPaper} onBack={() => {}} />,
    );
    screen.getAllByTestId("limitation").forEach((l) => {
      expect(l.closest("[data-print='hide']")).toBeNull();
    });
    expect(container).toBeTruthy();
  });
});

describe("the trial drill-down", () => {
  const injection = scripted.evidence.find(
    (e) => e.probe_id === "injection-resistance",
  )!;

  it("reaches the raw exchange from a finding in two clicks", () => {
    // Headline -> trials is the path that makes a finding challengeable.
    const opened: number[] = [];
    render(
      <RunDetail
        run={scripted}
        onBack={() => {}}
        onOpenTrials={(unit) => opened.push(unit)}
      />,
    );
    fireEvent.click(screen.getAllByTestId("open-trials")[0]!);
    expect(opened).toEqual([0]);
  });

  it("renders prompt, system prompt and response in full", () => {
    const { container } = render(
      <TrialDetail evidence={injection} onBack={() => {}} />,
    );
    const text = allText(container);
    const trial = injection.trials[0]!;
    // Whole strings, not excerpts. If these were truncated the reviewer
    // could not assess what was actually sent.
    expect(text).toContain(trial.prompt);
    expect(text).toContain(trial.response_text);
    if (trial.system) expect(text).toContain(trial.system);
    expect(text).not.toContain("…");
  });

  it("marks exceptions and shows the label that fired", () => {
    render(<TrialDetail evidence={injection} onBack={() => {}} />);
    const cards = screen.getAllByTestId("trial-card");
    const exceptions = cards.filter(
      (c) => c.getAttribute("data-exception") === "true",
    );
    expect(exceptions.length).toBe(2);
    const labels = within(exceptions[0]!).queryByTestId("trial-labels");
    expect(labels?.textContent ?? "").toMatch(/\w+:/);
  });

  it("can be narrowed to exceptions only", () => {
    render(<TrialDetail evidence={injection} onBack={() => {}} />);
    expect(screen.getAllByTestId("trial-card").length).toBe(
      injection.trials.length,
    );
    fireEvent.change(screen.getByLabelText("Filter trials"), {
      target: { value: "exceptions" },
    });
    expect(screen.getAllByTestId("trial-card").length).toBe(2);
  });

  it("says when the adapter reported no token usage rather than showing zero", () => {
    // D-037: absence is not zero, and is never replaced by an estimate.
    const noUsage = {
      ...injection,
      trials: [{ ...injection.trials[0]!, usage: undefined }],
    };
    const { container } = render(
      <TrialDetail evidence={noUsage} onBack={() => {}} />,
    );
    expect(allText(container)).toContain("usage not reported");
    expect(allText(container)).not.toContain("0+0 tokens");
  });

  it("states how many items were examined and how many were exceptions", () => {
    const { container } = render(
      <TrialDetail evidence={injection} onBack={() => {}} />,
    );
    expect(allText(container)).toMatch(/2 exception\(s\) in 22 item\(s\) examined/);
  });
});
