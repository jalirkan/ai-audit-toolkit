# Decision Ledger

Running log of design decisions worth not relitigating. Append as you go.

## D-001 · 2026-07-27 · Stdlib-first, mock-first
Procedures must run with zero install and zero network so they work in locked-
down audit environments and in CI. Real model adapters are optional and gated
behind an explicit key + operator action. Every probe is testable offline
against a scriptable mock.

## D-002 · 2026-07-27 · Evidence before conclusions
The unit of everything is `Evidence` (inputs, outputs, metric, uncertainty,
timestamps, model fingerprint), not a verdict. Reports render conclusions from
stored evidence; conclusions are never produced without the evidence trail.

## D-003 · 2026-07-27 · No copyrighted framework text
Framework catalogs store control IDs plus ORIGINAL one-line summaries. NIST AI
RMF / ISO 42001 / EU AI Act text is never reproduced. Verify before publishing.

## D-004 · 2026-07-27 · Uncertainty is mandatory
Scores carry confidence intervals and sample sizes. Bare percentages are a bug
(inherited discipline from the crypto-paper-trader project's stats rules).

## D-005 · 2026-07-27 · Tamper-evidence via hash chain
The journal hashes each row against the previous row's hash, so any edit to
history is detectable by `verify()`. This is what makes the output audit-grade.
