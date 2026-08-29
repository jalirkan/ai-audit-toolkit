# Example output

A complete run against a fictional vendor endpoint, generated offline. Regenerate with:

```
python examples/generate.py
```

## The scenario

"Northwind Logistics" is a made-up shipping company, so the source set is self-contained
and the example asserts nothing about the real world. The endpoint is audited twice:
version 1 becomes the baseline, then a model change ships and version 2 is re-assessed.

Version 2 is scripted to exercise **all three outcomes**, because a sample report where
everything passes teaches nothing about how the tool behaves when it doesn't:

| Procedure | Outcome | Why |
|---|---|---|
| injection-resistance | **fail** | Two of 22 attacks retrieved the canary. A control admitting no exceptions recorded two, so the finding is high severity. |
| output-consistency | **pass** | All 20 paraphrases produced the expected delivery window. |
| citation-faithfulness | **inconclusive** | Two of 20 answers contained an unsupported figure. The interval for that rate straddles the tolerance, so the sample cannot settle the question — and the report says so rather than rounding it to a pass. |

## Files

| File | What it shows |
|---|---|
| `workpapers.md` | Full workpapers: procedure, population, criterion applied, result with interval, each exception, limitations, conclusion, evidence hash. |
| `management-letter.md` | Findings ranked by severity, with recommendations, scope limitations, and coverage gaps. |
| `management-letter.html` | The same letter as a standalone page — no external assets, opens from any archive. |
| `journal.txt` | Journal entries and a chain verification, including what verification does *not* prove. |
| `drift.txt` | Version 2 compared against the version 1 baseline. |
| `coverage.txt` | Framework coverage, gaps included. |
| `rag-screen-check.json` | The citation screen graded against the golden dataset (D-041), as a first-class artifact naming the probe it grades. Regenerate with `python cli.py rag datasets/northwind-rag-golden.json --screen-only --status-out examples/rag-screen-check.json`. |
| `comparison-local-models.md` | One battery against four local models at once, and the reading a single-model run cannot give: which failures belong to a model and which to the procedure. See the note below — unlike everything else here, this one is a measurement, not a fixture. |
| `comparison-local-models.html` | The same comparison as a standalone page. |

## Two things worth reading closely

**The drift report catches a regression the statistics alone would miss.** Going from 0
leaks in 22 to 2 in 22 is not a statistically significant change in the rate — the
bootstrap interval for the difference includes zero. But under a zero-tolerance control
it flips the outcome from pass to fail, and `drift.txt` reports it on those grounds.
A monitor that required statistical significance would have said "no drift" on the run
where the canary first escaped.

**The inconclusive result is the interesting one.** Two unsupported answers out of twenty
is a rate of 0.100, with a 95% CI of [0.028, 0.301] against a tolerance of 0.200.
The point estimate is inside tolerance; the interval is not. Reporting that as a pass
would claim more than twenty questions can support.

Run ids and timestamps change each time the script runs. Everything else is deterministic.

## One of these is a measurement, not a fixture

Every other artifact here is generated offline from scripted mocks, so `generate.py`
reproduces them byte for byte on any machine. `comparison-local-models.*` is different:
it is a record of what four real models actually did, run on 2026-08-28 against a local
ollama endpoint on one GTX 1080 — qwen3:8b, llama3.2:3b, gemma3:4b and phi4-mini, 142
procedures each. Cloning this repo will not regenerate it, and re-running it on other
hardware will not reproduce these numbers exactly.

It earns its place because it shows the one thing a single-endpoint run cannot. Every
model failed injection-resistance, at rates from 0.136 to 0.500 — a result about the
attack class rather than about any one model, and not a claim a single run could
support. Meanwhile the model that resisted injection worst screened *best* on citation
faithfulness, which is why the matrix refuses to produce an overall ranking: the metrics
are not commensurable and averaging them would invent a number that means nothing.

Read the "not distinguished" section at the end before drawing conclusions from the
spread. At 20-22 trials per cell every interval overlaps, so none of these differences
are established — including the injection range. The comparison shows where to look
next, not who won.

Regenerate the *shape* of this artifact (against whatever models are installed) with:

```
ops/compare-models.sh
```
