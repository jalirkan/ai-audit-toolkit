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

## D-006 · 2026-07-27 · `core/` holds the shared domain model
PLAN's architecture sketch had no home for `Evidence`, which every other
package touches. It lives in `core/` (evidence, stats, canonical encoding),
which imports nothing else in the toolkit. The dependency arrow points one way:
adapters, probes, journal, drift, and report all depend on `core`, never the
reverse. `ModelFingerprint` sits in `core` rather than `adapters` for the same
reason — the journal must store fingerprints without importing adapter code.

## D-007 · 2026-07-27 · Generation parameters are adapter-level, not per-call
`complete()` takes a prompt and nothing else that affects sampling; temperature,
max_tokens, and seed are fixed when the adapter is constructed. If they could
vary per call, the fingerprint stored with the evidence would describe the run
only approximately. A probe needing a second configuration constructs a second
adapter, which makes the difference visible in the evidence. The system prompt
is the deliberate exception — varying it *is* the procedure for injection
testing — so it may be overridden per call and the effective value is recorded
on every `Trial`; the fingerprint hashes the adapter default.

## D-008 · 2026-07-27 · Uncertainty enforced at construction, via Wilson
D-004 is implemented structurally: a `Measurement` of kind `proportion` or
`mean` raises unless it has an interval, a named method, and a confidence
level, so no code path can emit a bare rate. `Measurement.render()` is the only
sanctioned way to display one and always includes n. Wilson score intervals are
the default for proportions rather than the normal approximation, because probe
results cluster at 0 and 1 (no injections leaked, every paraphrase agreed) where
the normal approximation reports zero width and implies certainty from eight
observations. `n=0` yields the interval [0, 1] and `is_informative == False` —
"not tested", never "0%". Bootstrap intervals are accepted for means and arrive
with drift in Phase 3.

## D-009 · 2026-07-27 · Canonical JSON is pinned; records are versioned
All hashing goes through `core.canonical`: sorted keys, tight separators,
UTF-8, and `allow_nan=False`. The hash chain in Phase 2 only means something if
one logical value always produces one byte sequence, so the encoding is fixed in
one place and covered by a known-vector test that fails loudly if it ever
changes. NaN and Infinity are rejected at record construction rather than at
write time, where the offending value is no longer traceable. Every evidence
record carries `schema_version`, and reading a record from a future version is
an error rather than a partial parse.

## D-010 · 2026-07-27 · Mock determinism extends to latency; fallback is neutral
The mock derives reported latency from a hash of the call rather than the
clock, so evidence records hash identically across runs and machines — without
that, Phase 2 could not test the journal against fixtures. Its answer to an
unmatched prompt is a visibly-mock echo that neither refuses nor agrees: a
default leaning either way would quietly decide the outcome of any probe whose
script had a gap, and that bug would present as a finding.
