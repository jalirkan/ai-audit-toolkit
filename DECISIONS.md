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

## D-011 · 2026-07-27 · Three outcomes, decided against the interval
`decide()` compares the interval to the threshold, not the point estimate, and
returns pass / fail / **inconclusive**. Pass means the whole interval sits on
the acceptable side; fail means it sits entirely on the other; a straddling
interval means the sample cannot answer the question and says so. 1 leak in 8
and 125 in 1000 are both 12.5% and only one warrants action — comparing point
estimates loses exactly that distinction. Inconclusive is reported, never
rounded to pass.

`min_sample` (default 20) gates only the *pass*. A procedure that finds real
exceptions in a small sample is evidence of a problem regardless of n; only the
reassuring conclusion has to earn its sample size.

## D-012 · 2026-07-27 · Zero-tolerance controls use attribute sampling
A control that admits no failures (system-prompt secrets must never leak) can
never pass under the interval rule, since a Wilson upper bound exceeds zero for
any finite sample. Threshold exactly 0.0 with `lower_is_better` therefore
selects the attribute-sampling rule instead: fail on any exception, pass on
zero exceptions once the sample meets the minimum, and report the interval
alongside so the reader can see what n buys. "No exceptions noted in a sample
of 22" with its interval is the auditor's sentence and it is honest; "0% leak
rate" is neither.

## D-013 · 2026-07-27 · Probes return one evidence record per unit tested
`run()` returns `list[Evidence]`, one per independently testable unit — a
question and its paraphrases, a system-prompt scenario and its attacks, a
source set and its questions. The unit id lands in `config["unit"]`. Batteries
aggregate across records rather than probes flattening the distinction, so a
single scenario failing is visible instead of averaged away.

## D-014 · 2026-07-27 · Citation conclusions rest on the answer-level rate
The citation probe reports two rates. The claim-level rate is finer-grained,
but sentences inside one answer are not independent draws — an answer that goes
off the rails yields five unsupported sentences at once, and scoring those as
five independent observations produces an interval narrower than the evidence
supports. The answer is the independent sampling unit, so the decision is made
on the share of answers containing at least one unsupported sentence. Both
rates are reported and `config["decision_metric"]` names the one the conclusion
used.

## D-015 · 2026-07-27 · Lexical screens are labelled as screens
There is no semantic model here. Consistency clustering, citation support, and
canary detection are all lexical, and each probe's `limitations` field states
what that costs — rendered into the workpaper next to the result, not buried in
a docstring. Two consequences are load-bearing: citation exceptions are
candidates for reviewer inspection rather than established findings (which is
why its tolerance is loose and D-012's zero-tolerance rule is deliberately not
used there), and the injection leak rate is a **lower bound**, since encoded or
acrostic exfiltration succeeds without being counted. Attacks of exactly those
kinds are in the default battery anyway, because a model that complies with
them usually also leaks in plainer ways.

## D-016 · 2026-07-27 · No composite score; battery outcome by precedence
A battery does not average its probes into an assurance score. A leak rate and
a paraphrase agreement rate measure different things on different populations
and their mean is not a quantity — and averaging hides the case a reader most
needs to see, one control failing badly among several passing. The rollup is a
distribution (counts by outcome) plus a single outcome chosen by precedence:
**fail > error > inconclusive > pass**. A detected deficiency leads because it
is the most actionable; an incomplete procedure outranks a completed one that
could not conclude. A battery is clean only when every probe concluded and
passed. A test asserts no `score`/`composite`/`average` attribute ever appears.

## D-017 · 2026-07-27 · The chain detects edits, not rewrites — anchor the head
The hash chain catches any edit, deletion, reordering, or insertion within a
journal. It cannot catch a **full rebuild**: anyone who can write the database
file can regenerate every row and hash into a chain that verifies perfectly.
The answer is anchoring — `Journal.head()` returns the value to record
somewhere the file's owner does not control, and `verify(expected_head=...)`
checks against it. A test asserts that a rebuilt journal verifies clean and is
caught only by the anchor, so the limitation stays documented in executable
form rather than drifting into an overclaim. Append-only SQLite triggers block
UPDATE and DELETE as a first line of defence; the tests drop them to simulate
an attacker with file access, which is the only honest way to test the chain.

## D-018 · 2026-07-27 · The run record is a manifest, not a copy
Journaling a run writes each evidence record as its own entry, then a run entry
listing their content hashes. The manifest binds specific evidence to a
specific run without duplicating it, so a substituted record fails to match,
and an interrupted run still leaves a trail of what completed. Evidence is
appended as it is produced, before the manifest.

## D-019 · 2026-07-27 · Suites are JSON data files
A battery is a JSON file naming probes and their configuration, so the suite an
engagement ran can be attached to the workpapers, diffed against last
quarter's, and re-run verbatim. JSON rather than YAML because YAML is a
dependency and D-001 makes dependencies earn their place; the loss is comments,
which `description` fields cover. Probe ids are resolved against the registry at
load time so a typo fails immediately with the list of valid ids, not half-way
through a run.

## D-020 · 2026-07-27 · An absent key and an empty one mean different things
Found while testing: `{"attacks": []}` was falling back to the 22 default
attacks because the code tested truthiness. Config handling now distinguishes a
missing key (use the default) from a key present but empty (the config is
wrong, raise). Silently running a procedure the auditor did not configure, and
then reporting evidence about it, is the class of bug this project can least
afford.

## D-021 · 2026-07-27 · Metric direction lives on the measurement
`Measurement` carries `direction` (lower/higher is better, or neutral) rather
than that knowledge staying inside the probe that produced it. Drift cannot
tell a regression from an improvement without it — the same +0.10 is bad for a
leak rate and good for an agreement rate — and reports cannot phrase a finding
without it. `decide()` cross-checks the direction it was passed against the
one the measurement declares and raises on disagreement, since that mismatch
would silently invert a conclusion.

## D-022 · 2026-07-27 · Drift is significance, not deltas
A metric counts as drifted only when the bootstrap interval for the difference
excludes zero. Every rate moves between runs; "leak rate went from 0.00 to
0.05" on 20 trials is one extra event and entirely consistent with no change.
Reporting raw deltas would make the monitor fire constantly and be ignored.

The bootstrap draws from `Binomial(n, p̂)` rather than resampling a list of
zeros and ones. For Bernoulli data these are the *same distribution*, not an
approximation — resampling n values from a vector with p̂ ones is exactly
binomial — so it is the identical procedure computed faster, which matters when
a battery yields many comparisons. Sampling is seeded, the seed is recorded
alongside every interval, and a test asserts the interval is stable across
seeds: an auditor re-running the analysis must get the same numbers.

Validated by planted-signal discipline in both directions. Two samples drawn
from the same distribution are flagged in 1–5% of trials against a nominal 5%
(conservative, the right way to err), and a genuine difference is caught in
over 95%. A detector that never fires would pass the first test alone.

## D-023 · 2026-07-27 · A worsened outcome is drift even without significance
`has_drift` is true when there is a significant regression **or** any unit
whose outcome got worse. The second clause matters because of D-012: under a
zero-tolerance control, 0/22 leaks to 1/22 flips pass to fail while being
nowhere near statistically significant. Requiring significance alone would
report "no drift" on the run where the canary first escaped. Outcome severity
ranks pass < inconclusive < error < fail.

## D-024 · 2026-07-27 · Comparisons state whether they are like-for-like
A drift number is only meaningful if the same procedure ran both times, so the
report names what changed rather than quietly comparing anyway: units added or
removed since the baseline, units whose probe configuration hash differs, and
the model fingerprint field by field. A changed fingerprint is usually the
reason for the comparison, but it is sometimes the explanation for a
"regression" that is really a different model. Baselines refuse to be
overwritten without an explicit flag — silently replacing the reference point
would make "no drift" true by construction.
