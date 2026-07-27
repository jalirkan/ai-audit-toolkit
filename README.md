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

## What it does (when complete)

- **Probe batteries** — repeatable test suites against any model endpoint:
  output consistency under paraphrase, prompt-injection resistance, citation
  faithfulness against provided sources, refusal-boundary consistency,
  PII-handling behavior, determinism configuration checks.
- **Tamper-evident evidence journal** — every probe run recorded with inputs,
  outputs, hashes, and timestamps in a hash-chained log; an audit trail an
  auditor would accept.
- **Drift monitoring** — re-run a battery after a model/prompt change and get
  a statistical comparison against the baseline (bootstrap CIs, not vibes).
- **Workpaper generation** — findings rendered as structured audit workpapers
  and a management-letter style report, with each test mapped to framework
  control references.
- **Adapter pattern** — Anthropic API, OpenAI-compatible endpoints, or the
  built-in mock model (all tests run offline against the mock).

## Status

Planning complete, build starting — see [PLAN.md](./PLAN.md) for the phased
roadmap and [DECISIONS.md](./DECISIONS.md) for the running decision ledger.

## Principles

Stdlib-first Python (no install step to run procedures), every capability
testable offline against the mock adapter, evidence before conclusions,
original text only (framework control IDs and short titles are referenced;
copyrighted framework text is never reproduced), and no real API calls unless
a key is present and the operator asked.

*Educational/professional tooling; not a substitute for a qualified audit.*
