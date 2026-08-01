/**
 * The evidence chain, and what verifying it does and does not prove.
 *
 * A hash chain detects edits, deletions, reordering and insertion. It cannot
 * detect a **full rebuild**: anyone able to write the database file can
 * regenerate every row and hash into a chain that verifies perfectly (D-017).
 * So the limitation is not a footnote here — it renders next to the result,
 * every time, and it comes from the server (`does_not_prove`) rather than
 * being retyped in the view, so the UI cannot show a clean verification
 * without it.
 *
 * The answer to a rebuild is anchoring: record the head somewhere the file's
 * owner does not control, and check against it later. That is what the
 * anchored-head field is for, and it is given equal weight to the verify
 * button rather than hidden behind an "advanced" disclosure, because it is the
 * only check that closes the gap.
 */

import { useState } from "react";
import { Caveat } from "../design/Outcome";
import { formatTimestamp, shortHash } from "../lib/format";

export interface JournalEntryPayload {
  seq: number;
  recorded_at: string;
  kind: string;
  payload_hash: string;
  prev_hash: string;
  entry_hash: string;
  payload_bytes: number;
}

export interface JournalEntriesPayload {
  entries: JournalEntryPayload[];
  head: string;
  total: number;
  limit: number;
  offset: number;
  genesis: string;
}

export interface VerificationPayload {
  ok: boolean;
  entries_checked: number;
  head_hash: string;
  expected_head: string | null;
  anchored: boolean;
  problems: { seq: number | null; code: string; detail: string }[];
  summary: string;
  does_not_prove: string;
}

function Copyable({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="inline-flex items-baseline gap-2">
      <code className="numeric break-all text-ink">{value}</code>
      <button
        data-print="hide"
        className="text-[10px] uppercase tracking-wider text-muted hover:text-ink"
        onClick={() => {
          void navigator.clipboard?.writeText(value);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1200);
        }}
        aria-label={`Copy ${label}`}
      >
        {copied ? "copied" : "copy"}
      </button>
    </span>
  );
}

const KIND_LABEL: Record<string, string> = {
  evidence: "Evidence record",
  run: "Run manifest",
  note: "Note",
};

export function JournalView({
  entries,
  verification,
  onVerify,
  verifying = false,
}: {
  entries: JournalEntriesPayload;
  verification: VerificationPayload | null;
  onVerify: (expectHead: string) => void;
  verifying?: boolean;
}) {
  const [anchor, setAnchor] = useState("");

  return (
    <div>
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Evidence journal</h1>
        <p className="mt-1 max-w-prose text-sm text-ink-soft">
          Every entry hashes the entry before it, so the log is a chain. An edit
          to any historical row breaks the link the next row asserts.
        </p>

        <div className="mt-5">
          <div className="text-xs uppercase tracking-wider text-muted">
            Head hash — record this somewhere outside this machine
          </div>
          <div className="mt-1" data-testid="head-hash">
            <Copyable value={entries.head} label="head hash" />
          </div>
          <p className="mt-2 numeric text-xs text-muted">
            {entries.total} entr{entries.total === 1 ? "y" : "ies"}
          </p>
        </div>
      </header>

      <section className="mb-10">
        <h2 className="text-sm font-semibold uppercase tracking-[0.12em] text-muted">
          Verification
        </h2>

        <div className="mt-4 flex flex-wrap items-end gap-3" data-print="hide">
          <label className="flex-1 text-xs uppercase tracking-wider text-muted">
            Anchored head (optional)
            <input
              type="text"
              value={anchor}
              onChange={(e) => setAnchor(e.target.value)}
              placeholder="sha256:… as you recorded it elsewhere"
              aria-label="Anchored head"
              className="mt-1 w-full border border-rule bg-raised px-2 py-1.5 font-mono text-xs text-ink"
            />
          </label>
          <button
            className="border border-rule px-3 py-2 text-xs uppercase tracking-wider text-ink-soft hover:border-rule-strong hover:text-ink"
            onClick={() => onVerify(anchor.trim())}
            disabled={verifying}
            data-testid="verify-button"
          >
            {verifying ? "Verifying…" : "Verify chain"}
          </button>
        </div>

        {verification && (
          <div className="mt-5" data-testid="verification-result">
            <p
              className={`text-sm ${verification.ok ? "text-pass" : "text-fail"}`}
              data-ok={verification.ok ? "true" : "false"}
            >
              <span aria-hidden="true" className="mr-2">
                {verification.ok ? "■" : "▲"}
              </span>
              {verification.summary}
            </p>

            <p className="mt-2 numeric text-xs text-muted">
              {verification.entries_checked} entries checked ·{" "}
              {verification.anchored
                ? "checked against an anchored head"
                : "no anchored head supplied"}
            </p>

            {verification.problems.length > 0 && (
              <ul
                className="mt-3 space-y-1 text-sm text-fail"
                data-testid="verification-problems"
              >
                {verification.problems.map((p, i) => (
                  <li key={i}>
                    {p.seq === null ? "journal" : `entry ${p.seq}`} — {p.code}:{" "}
                    {p.detail}
                  </li>
                ))}
              </ul>
            )}

            {/*
              Never omitted, and never behind a tooltip. A green result without
              this sentence is an overclaim about what the chain establishes.
            */}
            <div className="mt-4" data-testid="chain-limits">
              <Caveat>{verification.does_not_prove}</Caveat>
            </div>

            {!verification.anchored && (
              <p className="mt-3 max-w-prose text-sm text-ink-soft" data-testid="anchor-prompt">
                This check was made against the journal's own head. To close the
                rebuild gap, paste a head you recorded elsewhere — a ticket, a
                signed email, another system's log — into the field above and
                verify again.
              </p>
            )}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-4 text-sm font-semibold uppercase tracking-[0.12em] text-muted">
          Chain
        </h2>
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-rule-strong text-left text-xs uppercase tracking-wider text-muted">
              <th className="py-2 pr-4 font-medium">Seq</th>
              <th className="py-2 pr-4 font-medium">Recorded</th>
              <th className="py-2 pr-4 font-medium">Kind</th>
              <th className="py-2 pr-4 font-medium">Entry hash</th>
              <th className="py-2 font-medium">Links to</th>
            </tr>
          </thead>
          <tbody>
            {entries.entries.map((entry) => (
              <tr
                key={entry.seq}
                className="border-b border-rule align-top"
                data-testid="journal-entry"
                data-kind={entry.kind}
              >
                <td className="numeric py-2 pr-4 text-ink">{entry.seq}</td>
                <td className="numeric py-2 pr-4 text-ink-soft">
                  {formatTimestamp(entry.recorded_at)}
                </td>
                <td className="py-2 pr-4 text-ink-soft">
                  {KIND_LABEL[entry.kind] ?? entry.kind}
                </td>
                <td className="numeric py-2 pr-4 text-ink-soft">
                  {shortHash(entry.entry_hash)}
                </td>
                <td className="numeric py-2 text-muted">
                  {entry.prev_hash === entries.genesis
                    ? "genesis"
                    : shortHash(entry.prev_hash)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
