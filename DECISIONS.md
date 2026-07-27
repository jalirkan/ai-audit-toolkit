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

## D-025 · 2026-07-27 · D-003 is enforced structurally, not by discipline
Framework catalogs hold an identifier plus a one-line summary written for this
project. Two tests keep it that way: `MAX_SUMMARY_CHARS` (220) caps summary
length, which an original one-liner fits and pasted framework text does not,
and a scan rejects any quoted span of 60+ characters inside a catalog string —
quoting is how copyrighted text would most plausibly arrive. Both were checked
against a simulated paste to confirm they fire. Control identifiers were
verified against current sources on 2026-07-27 and each catalog records that
date, because framework identifiers change between editions and a reader
deserves to see how stale a mapping might be.

## D-026 · 2026-07-27 · Catalogs are partial and say so
Each catalog lists only the controls this toolkit can produce technical
evidence for. A control's absence is not a statement about it. Claiming
complete coverage of ISO 42001 Annex A or the AI Act would be a much bigger
assertion than the evidence supports, and would make every gap invisible.

## D-027 · 2026-07-27 · A mapping is not a compliance claim
Mapping a procedure to a control asserts the procedure produces evidence
*relevant* to that control — never that the control is satisfied. Controls
generally require governance, policy, and documentation no test harness can
supply. Consequences in the code: every mapping carries a written rationale (an
unargued mapping cannot be defended in review, so `ControlReference` refuses to
be constructed without one), coverage reports the outcome next to the mapping,
and a control evidenced by a *failing* probe is reported as
`tested-with-exceptions` — covered and failing, which is different from both
"covered" and "no evidence". Gaps are listed explicitly rather than omitted,
since a coverage report that showed only what was tested would flatter the
engagement.

## D-028 · 2026-07-27 · Capabilities map to controls alongside probes
Logging and monitoring controls are evidenced by the toolkit's journal, drift
comparison, and generated workpapers rather than by any probe, so mapping
sources are prefixed `probe:` or `capability:`. Capabilities yield status
`evidence-present`, never a pass: a journal existing shows the technical means
are in place, which is not a test result. The caller declares which
capabilities were in use, since a battery result cannot reveal whether a
journal was written.

## D-029 · 2026-07-27 · One document model, two renderers
Markdown and standalone HTML are both built from the same block structure
rather than one converted into the other. Converting would need a Markdown
parser — a dependency D-001 does not permit here — and would let the two
outputs drift apart in content. With one model they cannot: a section in one is
a section in the other. The HTML embeds its own CSS and fetches nothing; an
audit artifact that pulls a stylesheet from a CDN is one that renders
differently, or not at all, when opened from an evidence archive in five years.
A test asserts no `http://`, `https://`, `<script`, or `<link` appears in the
output.

## D-030 · 2026-07-27 · The no-bare-rates rule is checked at the rendering boundary
D-004 is enforced at construction, but rendering is where it could still be
lost. A test scans both formats, across passing, failing, and inconclusive
runs, and requires that any line containing a percent sign also contains an
interval — plus a companion test proving the scan actually fails on a planted
bare rate. Reports render rates as decimals so the only percent signs that
appear come from the interval itself.

## D-031 · 2026-07-27 · Complete examination, not a sample
The workpaper describes what these procedures actually do: a 100% examination
of a population the auditor configured, not a random sample of some larger
universe of inputs. Writing "sample of 22" would imply a sampling frame that
does not exist, and would invite the reader to generalise beyond the battery.
The intervals describe variability in the model's responses to *these* items;
they say nothing about inputs nobody thought to write down.

## D-032 · 2026-07-27 · Findings and observations are separated; severity is a printed rule
A finding is a deficiency — a procedure concluded and found the control
wanting. An observation is a scope limitation — a procedure could not conclude.
Listing the second among the first would inflate the report and mislead about
what was established, so they get separate sections and only findings carry a
severity. Severity is High when a zero-tolerance control recorded any
exception, or a rate failed with its whole interval at twice the tolerance or
worse; Medium otherwise. The rule is printed in the letter, because a severity
the reader cannot reconstruct is one they cannot challenge. Remediation text
lives on the probe class, next to the procedure that understands what its own
failure means.

## D-033 · 2026-07-27 · Real adapters are opt-in, with no fallback either way
`build_adapter` raises when a real adapter is named without its key in the
environment, rather than quietly using the mock. A silent downgrade would
produce evidence labelled with an endpoint it never reached, which is the one
failure mode that would make the whole journal untrustworthy. The mock is the
default everywhere including the CLI, and a test asserts it.

Real adapters use `urllib` — one POST with JSON in and JSON out does not earn a
dependency. Every request goes through an injectable `Transport`, which is what
lets the suite exercise request construction, parsing, errors, and retries with
no network and no key. Bounded retries on transient statuses only, with an
injectable sleep, so one 429 partway through a 22-call battery does not turn a
whole procedure into error evidence.

The API key is never rendered: not in the fingerprint, `describe()`, `repr`,
the recorded `raw` payload, or any error message. A test checks each surface,
plus a positive test that the key does reach the request headers — otherwise
the negative tests would pass on an adapter that simply never used it. An
evidence journal that captured a credential would be worse than no journal.

## D-034 · 2026-07-27 · Fixture endpoints are a first-class input
`--mock-script` loads a scripted endpoint from JSON, and
`suites/demo-endpoint.json` ships one. This started as a documentation problem:
the README quick start claimed an all-pass run, but running the default suite
against the bare mock fails two probes — a hash-echo does not know a fictional
shipping policy. Those failures are artifacts of the fixture, not findings, and
a tool selling evidentiary credibility cannot have fabricated red output as its
front door.

The shipped fixture is scripted to produce a failure, a pass, **and** an
inconclusive result, because a sample run that is all green teaches nothing
about how the tool behaves when it is not. `examples/generate.py` loads the same
fixture rather than rebuilding it, so the README and the committed artifacts
cannot diverge, and a test asserts the exact figures the README prints.

## D-035 · 2026-07-27 · Bootstrap intervals are widened to the analytic bound
*Amends D-022, which described the plain percentile bootstrap.*

Found during the final verification sweep, by cross-checking the bootstrap
against Newcombe's hybrid-score interval across 749 (successes, n) pairs: the
plain percentile bootstrap claimed significance where the analytic method would
not in 12 cases, and **every one involved an arm at 0 or n**.

The cause is structural. Resampling an all-zero sample can only produce zeros,
so a boundary arm contributes no variance and the interval for the difference
comes out too narrow. A clean baseline — zero exceptions — is exactly what an
auditor's reference run looks like, so the pathology sat on this toolkit's most
common comparison. Two clean runs of 22 were reporting a difference interval of
[0, 0]: certainty that the rates match, from 22 observations each.

The reported interval is now the percentile bootstrap widened to the Newcombe
interval wherever Newcombe is wider. One sentence to state, errs conservative,
and keeps the bootstrap, which is what generalises to non-proportion metrics
later. `BootstrapInterval.widened` records when it applied and the rendered
interval says so. Re-validated afterwards: zero anti-conservative cases across
the same grid, the false-positive rate still sits under the confidence level,
and decisive differences are still detected — conservatism is only acceptable
if it does not blind the detector.

The lesson worth keeping: the failure appeared in about 2% of pairs and only at
the boundaries. Spot-checking a handful of cases would have missed it. The grid
comparison against an independent method is now a test.

## D-036 · 2026-07-27 · Comparisons rank nothing and name what they cannot separate
`compare` runs one battery across several endpoints and produces no overall
ranking and no per-endpoint score, for the same reason a battery has no
composite (D-016): a leak rate and an agreement rate are not commensurable, and
which one matters depends on the workload — a judgment for the reader.

The addition that makes the table honest is the "not distinguished" list. A
reader handed point estimates will order them, so wherever every endpoint's
interval overlaps every other's, the matrix says the run has not shown them to
differ. Two endpoints at 1/20 and 2/20 look like a 2x difference and are not
distinguishable at all.

Latency is reported with a nonparametric bootstrap interval — the distribution
is skewed, so a normal approximation would misstate it. Token totals are absent
because nothing carries adapter usage counts onto the `Trial`, and the honest
fix is to extend `Trial` rather than approximate. Prices are absent by choice,
not omission: they change, they vary by contract, and a stale rate baked into
an audit artifact is worse than none.
