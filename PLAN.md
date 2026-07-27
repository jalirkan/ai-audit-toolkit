# Build Plan — AI Audit Toolkit

A phased roadmap a fresh Claude instance can execute mostly autonomously.
Each phase ends with passing tests and a commit. Stdlib-first; the only
optional dependency is network access to a real model, and every phase must
be fully testable offline against the mock adapter.

## Conventions (inherited from the workstation)

- Python standard library only where practical; justify any dependency.
- kebab-case dirs, `snake_case.py`, one package per concern.
- Every non-trivial module gets unit tests in `tests/`; run
  `python -m unittest discover -s tests -t .` — keep it green before each commit.
- Anything shown to a user carries its uncertainty (CIs, sample sizes), never
  bare percentages — same rule as the crypto project.
- Never reproduce copyrighted framework text (NIST AI RMF, ISO 42001, EU AI
  Act). Reference control IDs and write original one-line summaries only.
- Commit in logical chunks with clear messages. Update DECISIONS.md when a
  design choice is worth not relitigating.

## Architecture

```
ai-audit-toolkit/
  adapters/        model endpoints behind one interface (mock, anthropic, openai-compatible)
  probes/          individual test procedures, each returns structured Evidence
  battery/         compose probes into named suites; run + score
  journal/         hash-chained tamper-evident evidence store (SQLite)
  drift/           baseline vs rerun statistical comparison
  frameworks/      control catalogs (original summaries) + probe→control mapping
  report/          workpaper + management-letter renderers (markdown/html)
  cli.py           run a battery, show journal, diff a drift run, emit a report
  tests/
```

## Phase 0 — Foundation
- Repo scaffold, `adapters/base.py` (interface), `adapters/mock.py`
  (deterministic, scriptable, offline). `ModelResponse` dataclass.
- `Evidence` dataclass: probe id, inputs, raw outputs, metric(s), pass/fail,
  timestamps, model fingerprint.
- Tests: mock adapter determinism, Evidence serialization round-trip.

## Phase 1 — Probe framework + first probes
- `probes/base.py`: a Probe takes an adapter + config, returns list[Evidence].
- Implement: **consistency** (N paraphrases of one question → agreement score),
  **injection resistance** (canary-string exfiltration attempts → leak rate),
  **citation faithfulness** (claims checked against provided sources →
  unsupported-claim rate). Each with a documented scoring method + CIs.
- Tests: each probe against a mock scripted to pass, and one scripted to fail.

## Phase 2 — Battery + evidence journal
- `battery/`: named suites (YAML/JSON config), run all, aggregate scores.
- `journal/`: SQLite, each run appended with a hash chain (each row hashes the
  prior row + its payload). `verify()` detects any tampering. This is the
  audit-trail centerpiece — test insertion, verification, tamper detection.

## Phase 3 — Drift monitoring
- Store a battery result as a labeled baseline; re-run and compare per-probe
  with bootstrap CIs; flag statistically significant regressions.
- Tests: planted drift is detected; noise is not (mirror the crypto lab's
  planted-signal test discipline).

## Phase 4 — Frameworks + mapping
- `frameworks/`: original-summary control catalogs for NIST AI RMF, ISO 42001,
  EU AI Act (ID + short original description + which probes provide evidence).
- Probe→control coverage map; report "controls with no evidence" gaps.

## Phase 5 — Reporting
- Workpaper renderer: per-probe procedure/population/sample/result/exception.
- Management-letter renderer: findings ranked by severity, framework refs,
  recommendations. Markdown + standalone HTML.

## Phase 6 — CLI + real adapters + docs
- `cli.py`: `run <battery>`, `journal show|verify`, `drift <baseline>`,
  `report <run>`. Real adapters behind env keys, mock is always the default.
- End-to-end example run + sample generated report in `examples/`.

## Stretch
- Golden-dataset RAG faithfulness harness; multi-model comparison matrix;
  cost/latency accounting per battery; scheduled re-runs with alerting.

## Definition of done (v1)
A user points the toolkit at any model endpoint (or the mock), runs a named
battery, gets a tamper-evident evidence journal and a framework-mapped
workpaper + report — entirely offline if they choose. Every claim in the
output carries its uncertainty.
