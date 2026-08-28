# AI Audit Toolkit

Assurance procedures for AI systems, built by an auditor who codes: structured
probe batteries, tamper-evident evidence journals, drift monitoring, and
generated audit workpapers mapped to NIST AI RMF, ISO/IEC 42001, and the EU AI
Act.

**The thesis:** organizations are deploying LLM systems faster than anyone can
audit them. The assurance profession needs tooling that produces *evidence* —
populations, samples, procedures, exceptions, and conclusions that survive
review — not vibes about whether a chatbot "seems fine." This toolkit runs
repeatable technical procedures against any LLM endpoint and writes the
workpapers.

## Quick start

No install step, no dependencies, no API key. Developed and tested on Python 3.10.

```bash
python cli.py run suites/baseline.json --mock-script suites/demo-endpoint.json --report
```

That runs the default battery against a scripted offline endpoint, writes a
hash-chained evidence journal, and renders workpapers and a management letter.

```
baseline-assurance [FAIL] run 70ea7b2038be6d81 against mock:demo-vendor-assistant
  3 unit(s) tested -- fail=1  inconclusive=1  pass=1
  [        FAIL] injection-resistance -- leak_rate: 0.091 (95% CI [0.025, 0.278], 2/22)
  [        PASS] output-consistency -- expected_answer_rate: 1.000 (95% CI [0.839, 1.000], 20/20)
  [INCONCLUSIVE] citation-faithfulness -- unsupported_answer_rate: 0.100 (95% CI [0.028, 0.301], 2/20)
```

`demo-endpoint.json` is a fixture with deliberate weaknesses, so a first run
shows all three outcomes rather than a wall of green. The inconclusive one is
the most instructive: two unsupported answers in twenty is a rate of 0.100, but
the interval reaches 0.301 against a tolerance of 0.200, so twenty questions
cannot settle it — and the tool says so instead of rounding to a pass.

Run it without `--mock-script` and the bare mock fails two probes, because a
hash-echo does not know a fictional shipping policy. That is a property of the
fixture, not a finding, which is exactly why fixtures are an explicit input.

See [examples/](./examples/) for the full generated workpapers, letter, drift
comparison, and coverage report.

## Commands

```bash
python cli.py run suites/baseline.json         # run a battery
python cli.py run suites/baseline.json --report --format md html
python cli.py report <run-id>                  # re-render from a stored run
python cli.py journal show                     # list recorded evidence
python cli.py journal verify                   # check the hash chain
python cli.py journal verify --expect-head sha256:...
python cli.py baseline save <run-id> q3-2026   # label a run as a reference
python cli.py drift suites/baseline.json --baseline q3-2026
python cli.py monitor suites/baseline.json --baseline q3-2026
python cli.py rag datasets/northwind-rag-golden.json --screen-only
python cli.py probes -v                        # what procedures exist
python cli.py coverage suites/baseline.json    # projected framework coverage

# one battery across several candidate endpoints
python cli.py compare suites/baseline.json \
  --endpoint incumbent=mock:suites/demo-endpoint.json \
  --endpoint candidate=anthropic:claude-sonnet-4-5 --out runs
```

`compare` produces no overall ranking and no per-endpoint score. It lists the
outcomes and intervals side by side, and separately names the metrics where
every interval overlaps — because ordering those by point estimate would invent
a difference the sample does not support.

`rag --screen-only` scores planted gold answers against the citation screen with
no model calls — a planted-signal check of the lexical screen itself. Omit
`--screen-only` to run the same dataset's questions through the live citation
probe against an adapter.

**The shipped golden set fails, on purpose.** It contains the failure modes a
lexical screen cannot handle — correct paraphrase, entity swaps, subtle term
substitutions — and reports accuracy per category rather than as one number:

```
    [INCONCLUSIVE] verbatim          1.000 (95% CI [0.806, 1.000], 16/16)
    [        FAIL] paraphrase        0.000 (95% CI [0.000, 0.194], 0/16)
    [INCONCLUSIVE] unsourced-number  1.000 (95% CI [0.806, 1.000], 16/16)
    [INCONCLUSIVE] negation-flip     1.000 (95% CI [0.806, 1.000], 16/16)
    [        FAIL] entity-swap       0.000 (95% CI [0.000, 0.194], 0/16)
    [        FAIL] term-swap         0.000 (95% CI [0.000, 0.194], 0/16)
```

An earlier version of this dataset reported 100% accuracy, because it contained
only cases the screen handles — it was measuring the dataset, not the method.
An aggregate over a golden set is a weighted average across whatever mix the
author wrote, and deleting the hard items would raise it while changing nothing
real. So the hard items stay, the score is stratified, and the verdict is taken
per category. Use this to know where the screen can be relied on, not to certify
it.

The five categories reading `INCONCLUSIVE` are perfect scores, not near misses.
Sixteen out of sixteen puts the Wilson lower bound at 0.806 against a required
0.900; clearing that line takes 35 items in a category, and six source sentences
do not yield 35 independent ones. Padding the category with rewordings would
move the bound without adding evidence, so the harness says inconclusive and
means it (D-046).

`monitor` is the cron-friendly drift wrapper: same comparison as `drift`, plus a
status JSON (default `runs/monitor-status.json`) and exit code 4 when drift is
detected. Example crontab (daily 06:00) and Windows Task Scheduler:

```cron
0 6 * * * cd /path/to/ai-audit-toolkit && python cli.py monitor suites/baseline.json --baseline q3-2026 --status-out runs/monitor-status.json || mail -s "AI audit drift" you@example.com < runs/monitor-status.json
```

```powershell
# Task Scheduler action (adjust paths):
python C:\Users\jalir\Projects\ai-audit-toolkit\cli.py monitor suites\baseline.json --baseline q3-2026
# Alert on exit code 4 (EXIT_DRIFT) in the task's settings or a wrapper script.
```

To test a real endpoint, name the adapter and set its key. There is no fallback
in either direction — asking for a real adapter without a key is an error, never
a quiet downgrade to the mock, because evidence must not carry the name of an
endpoint it never reached.

```bash
export ANTHROPIC_API_KEY=...
python cli.py run suites/baseline.json --adapter anthropic --model claude-sonnet-4-5

export OPENAI_API_KEY=...           # OPENAI_BASE_URL for a compatible server
python cli.py run suites/baseline.json --adapter openai
```

## Reading the evidence in a browser

```bash
python serve.py            # http://127.0.0.1:8770, read-only
```

A local reviewer front end over the same stored evidence: runs index, run
detail, printable workpapers, the individual model calls behind every finding,
framework coverage, drift, endpoint comparison, and the journal chain with its
verification.

It is built to be *argued with*. Every headline result reaches the exact
prompt, system prompt and response that produced it in two clicks, with the
evidence hash that ties it to the journal. The signature component is an
interval plotted against its criterion — whether it clears the line, sits past
it, or straddles it *is* the pass/fail/inconclusive decision, so the drawing
shows the reader what the conclusion rests on rather than asking them to take
it. Inconclusive is drawn as absence — hatched and unfilled — never as a shade
between pass and fail.

The Python side gains **zero** dependencies: `serve.py` is standard library
only, binds `127.0.0.1`, serves GET and HEAD and nothing else, and a test
fails the build if any import resolves into site-packages. The front end lives
under `web/` with its own npm toolchain, per the workstation rule that
dependencies belong where rewrites are cheap.

```bash
cd web && pnpm install && pnpm build   # then python serve.py serves it
cd web && pnpm dev                     # dev server, proxies /api to serve.py
cd web && pnpm test                    # 114 tests
```

Three of those tests carry the thesis rather than coverage: no rate renders
without its interval and sample size, no view invents an aggregate, and the
inconclusive treatment is distinguishable from failure by shape as well as
hue. Each has a companion test proving the check can actually fail.

## What it does

- **Probe batteries** — repeatable procedures against any endpoint: output
  consistency under paraphrase, prompt-injection resistance via canary
  exfiltration, and citation faithfulness against provided sources.
- **Tamper-evident evidence journal** — every run appended to a hash-chained
  SQLite log with append-only triggers. `verify()` detects any edit, deletion,
  or reordering, and reports every problem rather than the first.
- **Drift monitoring** — baseline a run, re-run after a change, and get a
  bootstrap comparison that flags statistically significant regressions.
  `monitor` writes a status file for external schedulers (cron / Task
  Scheduler); there is no built-in daemon or emailer.
- **Golden RAG harness** — closed-context dataset of sources + labeled gold
  answers for planted-signal checks of the citation screen
  (`datasets/northwind-rag-golden.json`). Deliberately includes the cases the
  screen gets wrong, and scores per category so they cannot be averaged away.
  Not a retrieval engine.
- **Workpaper generation** — procedure, population, criterion, result,
  exceptions, limitations, and conclusion per unit tested, plus a
  management letter with findings ranked by a stated severity rule. Markdown
  and standalone HTML.
- **Framework mapping** — partial, dated control catalogs with coverage
  reporting that names the gaps.
- **Adapters** — Anthropic, any OpenAI-compatible endpoint, and a deterministic
  mock. Every capability is testable offline against the mock.

## What it does not do

Worth saying plainly, because assurance tooling that overclaims is worse than
none:

- **A mapping is not a compliance claim.** Mapping a probe to a control means
  the procedure produces evidence *relevant* to it, never that the control is
  satisfied. Controls need governance, policy, and documentation no test
  harness supplies.
- **The screens are lexical.** There is no semantic model here. Consistency
  clustering, citation support, and canary detection are all token matching.
  Each probe states what that costs, and those statements are rendered into the
  workpaper next to the result. The citation screen is 0-for-16 on paraphrase,
  entity swaps, and term swaps in the shipped golden set — measured, not
  estimated. It does catch invented figures and inverted polarity.
- **The injection leak rate is a lower bound.** Encoded or acrostic
  exfiltration succeeds without being counted.
- **The hash chain detects edits, not rewrites.** Anyone who can rewrite the
  database can rebuild a chain that verifies. Record `journal head` somewhere
  else; that is what the anchor is for.
- **A clean run is a narrow claim.** It says these procedures found no
  exceptions in this population — not that the weakness is absent.

## Principles

Standard library only, so procedures run in locked-down environments with no
install step. Every capability testable offline against the mock. Evidence
before conclusions. Original text only — framework control IDs and short
original summaries are referenced; copyrighted framework text is never
reproduced, and two tests enforce it. **Every score carries a confidence
interval and a sample size**; a `Measurement` of a rate cannot be constructed
without one, and a renderer test rejects any output line with a bare
percentage.

Three outcomes, not two: a procedure whose interval straddles its threshold
reports **inconclusive** rather than rounding to a pass.

## Layout

```
adapters/    endpoints behind one interface (mock, anthropic, openai-compatible)
core/        evidence records, statistics, canonical encoding — depends on nothing
probes/      test procedures; each returns Evidence, never a verdict
battery/     named suites defined as JSON data; run and roll up
journal/     hash-chained SQLite evidence store
drift/       baselines and bootstrap comparison
frameworks/  control catalogs (original summaries) and coverage mapping
compare/     one battery across several endpoints, side by side
rag/         golden-dataset loader and citation-screen harness
datasets/    shipped golden RAG faithfulness set
report/      one document model, rendered to Markdown, standalone HTML, and JSON
suites/      battery specs
examples/    a generated end-to-end run
cli.py
serve.py     read-only localhost API over stored evidence (stdlib only)
web/         reviewer front end (Vite/React/TS/Tailwind); its own npm toolchain
tests/       unit tests; the whole suite runs offline with no key
```

## Tests

```bash
python -m unittest discover -s tests -t .
```

The suite requires no network and no API key. That is a hard constraint, not a
convenience: procedures that cannot be verified in a locked-down environment
cannot be trusted in one.

## Status

Phases 0–6 of [PLAN.md](./PLAN.md) complete, plus F0–F4 of
[FRONTEND-BRIEF.md](./FRONTEND-BRIEF.md) — the read-only API and the reviewer
front end. 718 Python tests, 114 front-end tests. Design decisions worth not
relitigating are in [DECISIONS.md](./DECISIONS.md).

*Educational/professional tooling; not a substitute for a qualified audit.*
