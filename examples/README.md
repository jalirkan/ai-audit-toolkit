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
