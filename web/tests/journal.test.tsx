/**
 * The journal view.
 *
 * One property carries this screen: a clean verification must never appear
 * without the sentence saying what it does not establish. D-017 is the
 * project's most easily overclaimed result — "chain intact" reads like proof
 * of integrity, and it is proof of *internal consistency* only. A rebuilt
 * journal verifies perfectly.
 *
 * So the tests check that the limitation renders on a passing verification,
 * that it comes from the server rather than being retyped in the view, and
 * that an un-anchored check says it was un-anchored.
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import entriesFixture from "./fixtures/journal-entries.json";
import verifyFixture from "./fixtures/journal-verify.json";
import verifyAnchoredFixture from "./fixtures/journal-verify-anchored.json";

import {
  JournalView,
  type JournalEntriesPayload,
  type VerificationPayload,
} from "../src/views/JournalView";

const entries = entriesFixture as unknown as JournalEntriesPayload;
const verified = verifyFixture as unknown as VerificationPayload;
const anchored = verifyAnchoredFixture as unknown as VerificationPayload;

describe("the chain", () => {
  it("shows the head hash prominently and copyably", () => {
    render(
      <JournalView entries={entries} verification={null} onVerify={() => {}} />,
    );
    const head = screen.getByTestId("head-hash");
    expect(head.textContent).toContain(entries.head);
    expect(screen.getByLabelText("Copy head hash")).toBeInTheDocument();
  });

  it("says the head should be recorded off this machine", () => {
    const { container } = render(
      <JournalView entries={entries} verification={null} onVerify={() => {}} />,
    );
    expect(container.textContent).toMatch(/record this somewhere outside this machine/i);
  });

  it("lists entries in sequence with their kinds", () => {
    render(
      <JournalView entries={entries} verification={null} onVerify={() => {}} />,
    );
    const rows = screen.getAllByTestId("journal-entry");
    expect(rows).toHaveLength(entries.entries.length);
    const kinds = new Set(rows.map((r) => r.getAttribute("data-kind")));
    expect(kinds.has("evidence")).toBe(true);
    expect(kinds.has("run")).toBe(true);
  });

  it("marks the first entry as chaining off genesis", () => {
    const { container } = render(
      <JournalView entries={entries} verification={null} onVerify={() => {}} />,
    );
    expect(container.textContent).toContain("genesis");
  });
});

describe("verification states its limits", () => {
  it("renders the limitation alongside a clean result", () => {
    expect(verified.ok).toBe(true);
    render(
      <JournalView entries={entries} verification={verified} onVerify={() => {}} />,
    );
    const limits = screen.getByTestId("chain-limits");
    expect(limits.textContent).toMatch(/full rebuild/i);
    expect(limits.textContent).toMatch(/outside this machine|recorded outside/i);
  });

  it("takes the limitation from the payload rather than retyping it", () => {
    // If the server's wording changes, the screen changes with it. A copy in
    // the view is a copy that drifts from what the engine actually claims.
    render(
      <JournalView entries={entries} verification={verified} onVerify={() => {}} />,
    );
    expect(screen.getByTestId("chain-limits").textContent).toBe(
      verified.does_not_prove,
    );
  });

  it("cannot show a passing result without the limitation", () => {
    const { container } = render(
      <JournalView entries={entries} verification={verified} onVerify={() => {}} />,
    );
    const result = screen.getByTestId("verification-result");
    expect(result.querySelector("[data-testid='chain-limits']")).not.toBeNull();
    expect(container.textContent).toContain("Chain intact");
  });

  it("says when no anchored head was supplied, and how to close the gap", () => {
    render(
      <JournalView entries={entries} verification={verified} onVerify={() => {}} />,
    );
    expect(verified.anchored).toBe(false);
    const prompt = screen.getByTestId("anchor-prompt");
    expect(prompt.textContent).toMatch(/close the rebuild gap/i);
  });

  it("reports an anchored check as anchored", () => {
    expect(anchored.anchored).toBe(true);
    const { container } = render(
      <JournalView entries={entries} verification={anchored} onVerify={() => {}} />,
    );
    expect(container.textContent).toMatch(/checked against an anchored head/i);
    expect(screen.queryByTestId("anchor-prompt")).toBeNull();
  });

  it("passes the anchored head through to the caller", () => {
    const onVerify = vi.fn();
    render(
      <JournalView entries={entries} verification={null} onVerify={onVerify} />,
    );
    fireEvent.change(screen.getByLabelText("Anchored head"), {
      target: { value: `  ${entries.head}  ` },
    });
    fireEvent.click(screen.getByTestId("verify-button"));
    expect(onVerify).toHaveBeenCalledWith(entries.head);
  });

  it("lists every problem when the chain is broken", async () => {
    const broken: VerificationPayload = {
      ...verified,
      ok: false,
      summary: "Chain BROKEN: 2 problem(s) across 4 entries.",
      problems: [
        { seq: 2, code: "payload-modified", detail: "stored payload no longer matches" },
        { seq: null, code: "head-mismatch", detail: "head differs from the anchor" },
      ],
    };
    render(
      <JournalView entries={entries} verification={broken} onVerify={() => {}} />,
    );
    await waitFor(() => {
      const problems = screen.getByTestId("verification-problems");
      expect(problems.textContent).toContain("payload-modified");
      expect(problems.textContent).toContain("head-mismatch");
      // Reported against the journal, not a row, when there is no sequence.
      expect(problems.textContent).toContain("journal —");
    });
    // The limitation still renders on a failure.
    expect(screen.getByTestId("chain-limits")).toBeInTheDocument();
  });

  it("distinguishes pass from fail by glyph, not only colour", () => {
    const { container: ok } = render(
      <JournalView entries={entries} verification={verified} onVerify={() => {}} />,
    );
    const { container: bad } = render(
      <JournalView
        entries={entries}
        verification={{ ...verified, ok: false, summary: "Chain BROKEN" }}
        onVerify={() => {}}
      />,
    );
    expect(ok.textContent).toContain("■");
    expect(bad.textContent).toContain("▲");
  });
});
