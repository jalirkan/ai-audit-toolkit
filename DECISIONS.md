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
is skewed, so a normal approximation would misstate it. Token totals sum what
adapters reported onto each `Trial`; calls with no usage are counted as absent
rather than filled with zeros or estimates. Prices are absent by choice, not
omission: they change, they vary by contract, and a stale rate baked into an
audit artifact is worse than none.

## D-037 · 2026-07-27 · Token usage is first-class on Trial; absence is not zero
Adapters already return usage on `ModelResponse`. Probes copy it onto `Trial`
so the journal and aggregates see the same figures. `usage=None` means the
adapter reported nothing for that call — distinct from a reported zero, and
never replaced by an estimate (word-count proxies included). Serialization
omits the field when unreported so records written before this field keep a
stable content hash on round-trip; when present, only keys the adapter
actually sent are stored. Remote parsers follow the same rule: a missing
provider usage object becomes an empty mapping, not invented zeros. Compare
aggregates sum reported trials and print coverage (`k/n calls reported
usage`) so a partial total cannot be read as complete.

## D-038 · 2026-07-27 · Golden RAG is a planted-signal harness, not a second probe
The stretch "RAG faithfulness harness" reuses the citation lexical screen. The
dataset supplies closed-context sources (auditor-chosen chunks standing in for
retrieval) plus gold answers labeled `faithful` / `unfaithful` for what the
*screen* should conclude — not a model verdict and not a retrieval ranking.
`rag --screen-only` scores those gold answers offline with Wilson accuracy and
confusion tallies; live mode builds a `CitationCase` and runs
`citation-faithfulness`. There is no embedding retriever and no second
faithfulness metric that could drift from the citation procedure.

## D-039 · 2026-07-27 · Monitor is drift for external schedulers
Always-on alerting does not fit the stdlib, no-install constraint. `cli.py
monitor` re-runs a suite against a saved baseline (same comparison as `drift`),
writes a machine-readable status JSON, and exits `EXIT_DRIFT` when
`has_drift` is true. Cron or Task Scheduler owns the schedule and the alert
channel; this toolkit does not daemonize, mail, or page.

## D-040 · 2026-07-28 · The citation screen checks polarity explicitly
*Amends D-015, which described the screen as pure token overlap.*

Negation words are stopwords. That is right for similarity — "the cat sat" and
"the cat did not sit" concern the same subject — and it is the worst possible
default for a *support* check, because a claim and its exact negation then
match the same source equally well. "Northwind does ship hazardous materials"
scored as fully supported against a source stating it does **not**.

For a control that exists to catch fabricated claims about policy, silently
passing an inverted safety statement is the highest-consequence error available
to it. `assess_claim` now compares the polarity of the claim against the source
it matched, and only where coverage would otherwise have passed — a
low-coverage sentence is already unsupported and should say so for that reason.
Measured on the golden dataset, negation flips caught went from 2 of 8 to 8 of
8, with no new false positives on legitimately negated claims.

`is_negated` is coarse: it detects that a negation is present, not what it
scopes over, so a sentence that negates one clause and asserts another reads as
negated throughout. Documented rather than hidden. The alternative — ignoring
polarity — is not a more conservative choice, it is a wrong one.

## D-041 · 2026-07-28 · A golden dataset must contain the method's blind spots
*Amends D-038, which established the harness but not what belongs in it.*

The shipped dataset reported **100% screen accuracy, zero false positives, zero
false negatives**. The same screen, put to six realistic cases drawn from the
failure modes `probes/citation.py` already documented, got **none of them
right**. The dataset was composed almost entirely of near-verbatim answers and
answers containing invented numbers — the two things a lexical screen trivially
handles. It was measuring the dataset, not the method, and it produced a
confident figure while doing so. That is worse than having no harness at all.

Two rules follow.

**The dataset carries the cases the method fails.** Paraphrase, entity swap,
and term swap are in it, labelled `known_screen_miss`, and they stay there.
Deleting them would raise the headline number and change nothing real. An item
that fails *without* being marked as a known blind spot is surfaced separately
as a surprise, because that is new information about the screen.

**Accuracy is reported per category and decided per category.** An aggregate
over a golden dataset is a weighted average across whatever mix the author
wrote, movable at will by adding easy items. Per-category accuracy is not, and
the rollup is by precedence exactly as a battery is (D-016): fail if any
category fails. The overall figure is still shown, labelled as
composition-dependent, so a reader who wants one number gets one with the
warning attached.

The shipped dataset now reads: 1.000 on verbatim, unsourced-number,
negation-flip, abstention, and off-topic; **0.000 on paraphrase, entity-swap,
and term-swap**; overall FAIL. That is the truth about a lexical screen, and it
is what the workpapers should have been saying all along.

The per-stratum minimum sample is 8 rather than 20: a category the screen
cannot do at all fails decisively at 0/8 (upper bound near 0.32), while 8/8
still reads inconclusive, so the harness will not certify a category from a
handful of easy examples.

## D-042 · 2026-07-31 · The comparison payload carries its measurements
*Amends D-036, which established the "not distinguished" list but serialized
only its names.*

`ComparisonMatrix.to_dict()` reported which metrics failed to separate the
endpoints as bare `{probe_id, unit, metric}` triples. The intervals that
produced that judgment were dropped. Found while specifying the read API: the
one screen that best expresses D-036 — overlapping intervals drawn together on
a shared scale — could not be built from the payload that D-036 exists to
produce.

A consumer told only that `leak_rate` did not separate two endpoints has to
take it on faith. Shown 0.091 (95% CI [0.025, 0.278]) against 0.000 (95% CI
[0.000, 0.149]) on one axis, a reader sees *why* the run did not distinguish
them, and can disagree. The second is the whole point; the first is an
assertion.

`to_dict` now emits `metric_rows`, each carrying every endpoint's full
`Measurement` plus `all_overlap` and the overlapping labels.
`undistinguished_metrics` keeps its original shape, so the change is purely
additive and no existing consumer breaks.

The alternative considered and rejected was letting the client recompute
overlap from the run payloads. It needs no Python change, and it would put the
rule that decides what a run did and did not establish into a second language
where it can drift. `overlapping_labels()` stays the single home, and a test
asserts `metric_rows` and `undistinguished_metrics` never disagree.

## D-043 · 2026-07-31 · The web front end reads through a stdlib, loopback, read-only API
`serve.py` exposes stored evidence over HTTP so a reviewer can drill from a
headline result to the model call behind it. Four properties are structural
rather than policy, because each one is a place this could quietly stop being
audit tooling.

**Read-only by construction.** Only GET and HEAD are dispatched; there is no
write handler to guard, and unsupported methods fall through to the stdlib's
501. Running a battery costs money against a real endpoint, and a browser is
the wrong place to decide to spend it. `POST /api/runs` is deferred to a phase
that has to argue for itself, not disabled behind a flag someone can flip.

**Payloads are the engine's own `to_dict()` output, byte for byte.** Those
shapes are versioned and covered by the existing suite; reshaping them at the
boundary would create a second, untested contract that drifts from the first.
A test asserts the run payload equals the stored file exactly. The one
projection is the runs index, which exists so listing runs does not ship every
trial of every run, and it carries outcome counts rather than anything
resembling a summary figure. Where the engine has no serializer —
`VerificationResult`, `JournalEntry` — this module defines one, because there
is nothing to preserve.

**Schema versions travel in a header.** `Evidence` and `BatteryResult` carry
`schema_version` in the payload; `CoverageReport`, `DriftReport`, and
`ComparisonMatrix` do not. Wrapping every response in an envelope to fix that
would reshape the payloads, so the version map goes in `X-Engine-Schema` and
in `/api/meta`, and the client refuses what it cannot read — the same stance
`Evidence.from_dict` takes.

**No parameter reaches a path.** Run ids match the exact 16-hex form
`make_run_id` produces, baseline labels reuse `validate_label`, and suites and
datasets are resolved by enumerating their directory and matching a basename.
Traversal is not filtered or escaped; it is unrepresentable, because no
supplied string is ever joined to a `Path`. Tested with the encoded, absolute,
and null-byte variants rather than the obvious one.

Two carried obligations are enforced at the boundary rather than left to the
views: coverage responses always carry the D-027 disclaimer, and verification
responses always carry the D-017 limitation, so no client can render a green
tick without the sentence that qualifies it.

There is no authentication and none is wanted. The server binds `127.0.0.1`
and refuses to bind anything else, so reaching it already requires code
execution on the machine holding the evidence. A login form in front of a file
the user can open with `cat` is theatre, and tooling that performs security it
does not have is worse than tooling that states its boundary.

D-001 gets an executable guard: a test parses `serve.py`'s imports and fails if
any resolves into site-packages. A dependency added to the Python side would
take the engine's zero-install guarantee with it, and that is too important to
leave to a reviewer noticing a diff.

## D-044 · 2026-08-01 · The workpaper gets a third renderer, not a second implementation
*Extends D-029, which established one document model with two renderers.*

The web workpaper renders `report/document.py`'s block structure, served as
JSON by `/api/runs/:id/workpaper`. It is a third renderer over the model
`build_workpapers` already produces, not a React reimplementation of what a
workpaper contains.

D-029's argument was that converting Markdown into HTML would let the two
outputs drift apart in content. The argument is stronger for a third surface:
the workpaper a reviewer reads on screen and the one they print must be the
same document, or the tool has two accounts of the same evidence. With one
model they cannot diverge — a section missing from the screen is missing from
the Markdown too, and a test asserts the sections are all present.

The deciding constraint was the evidence hash. `Evidence.content_hash()` is
computed over canonical JSON (D-009) and is not carried in the payload.
Recomputing it in the browser would mean reimplementing `core.canonical` in
TypeScript, where a disagreement about key ordering or separators yields a
hash that is wrong but looks right — the worst available failure for the one
field whose entire job is tying a finding to the journal. The server computes
it, because the server is where the canonical encoder lives.

The endpoint also passes the current journal head into the document, so a
printed workpaper carries the value a reviewer would anchor (D-017). Absent
when no journal exists, rather than faked.

## D-045 · 2026-08-28 · "Answer:" restatements waive coverage, not the figures screen
Reasoning models close with a terse restatement of the answer ("Answer: 14
days."). Its claims were already made and screened in the body, and a
restatement is structurally too short to clear token coverage, so scoring it
manufactures exceptions that measure answer style, not faithfulness (observed
live against qwen3:8b, runs 6147e14a and 0b2f0853). Sentences opening with
"Answer:" or carrying "the answer is" are skipped as `skipped-restatement` --
after the unsourced-figure check, so a smuggled number is still caught.
Trade-off accepted: a wrong but figure-free restatement escapes the screen;
the body claims it restates do not.

## D-046 · 2026-08-28 · Equal strata of 16; a perfect category still reads inconclusive
*Amends D-041, which set the strata but not their size.*

The golden set carried 8 items in most categories and 4 in abstention and
off-topic. Every category the screen handles therefore scored a perfect 1.000
and reported **inconclusive**, because a Wilson lower bound on 8/8 is 0.676
against a required 0.900. The set now carries **16 items in each of the eight
categories**, 128 in total.

Equal strata are the point of the sizing, not a tidiness preference. The
overall figure is an unweighted mean over the eight failure modes rather than
an accident of how many of each got written, which is what D-041 objected to in
the first place. The two categories that were at 4 were the weakest evidence in
the file and had no business being the smallest.

**Doubling does not make the passing categories pass, and that is worth stating
plainly.** For a category with no misses the Wilson lower bound is `n / (n +
z²)`, so at 95% confidence it clears 0.900 only from **n = 35**. Sixteen buys
0.806, up from 0.676. The five categories the screen handles still read
inconclusive, and the honest description of that result is "16 out of 16, and
16 is not enough", not "nearly passing".

Padding to 35 was available and refused. Six source sentences carry perhaps a
dozen atomic facts; reaching 35 verbatim items means three or four rewordings
of each, and near-duplicate items are not independent draws. The interval would
narrow while the evidence stayed where it was — manufacturing a pass by
composition, which is the exact move D-041 exists to forbid, taken from the
other end. Lowering `DEFAULT_MIN_SCREEN_ACCURACY` or raising
`DEFAULT_STRATUM_MIN_SAMPLE` would have been the same trick in a different
place. Neither was touched.

Certifying a category the screen handles needs a wider closed-context corpus,
not more variants of the same six sentences. Until someone writes one, the
harness reports inconclusive, which is a true statement about what this dataset
can support.

The failing categories tightened for free: 0/16 puts the upper bound at 0.194,
down from 0.324, so `paraphrase`, `entity-swap`, and `term-swap` fail on
stronger evidence than before. All 56 pre-existing items keep their exact
screen outcomes; the sources were not touched, because changing them would
re-screen every item already in the file.
