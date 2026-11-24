# Studio capability benchmark

> Drives the real Studio API as a restricted user over hundreds of questions
> and multi-turn sessions, records every event, and scores accuracy,
> intelligence, UI output, process/latency and governance.

The harness lives in `backend/tests/benchmark/studio_capability/`. What it
covers is listed in the [capability map](CAPABILITY_MAP.md). Results are
written to a directory **outside** the repository; nothing a run produces is
committed.

## Corpus

| File | Content |
| --- | --- |
| `corpus/questions.jsonl` | 373 single questions, 43% Indonesian |
| `corpus/sessions.jsonl` | 38 sessions of 3–6 turns (133 turns); most turns carry a `fresh` twin |
| `corpus/ground_truth.json` | Each ground-truth SQL run as the administrator and as the user |

`corpus_build.py` generates the two JSONL files (edit it, then rerun) and
stamps `corpus_version`. Each unit has an `id`, `category`, `lang`,
`difficulty`, `expect_ui` (components a correct turn shows),
`expect_tools`, `expect_outcome` (`answer`, `clarify`, `refuse`,
`limitation`, `cancelled`; Studio shows no approval cards, so one appearing is
scored as a defect), optional `gt` (SQL plus which numbers
must appear), `leak_sql` for governance probes, `hitl` (how the runner answers
cards) and `tags` (`typo`, `date_relative`, `multi_step`, `ambiguous`,
`governance`, …). Optional fields: `expect_absent` (elements that must not
appear, for example a page for a one-figure question, or `artifact_saved`:
nothing is saved without the user), `expect_numbers` (figures from the MCP
test service that a correct answer states), `expect_tool_failure` (a tool that
fails on purpose) and `agent: "fixture"` (runs on the disposable fixture agent).

Element names in `expect_ui`/`expect_absent` include `text`, `table`, `chart`,
`chart:<kind>`, `kpi`, `map`, `page`, `section`, `slider`, `interactive`,
`place_map`, `route`, `route:alternatives`, `route:unavailable`,
`route:optimized`, `places:3+`, `picture`, `picture:shown`, `citation`,
`artifact_preview`, `save_suggestion`, `artifact_saved`, `hitl:<kind>` and
`suggestions`.

Ground truth is computed with direct, read-only SQL through Nova's SQL route,
as the administrator and as the user with their active role, so Ranger row
filters and masks apply to the expected values. Values only the administrator
can see become leak markers. `ground_truth.py --check` fails when the seeded
data no longer matches the committed file.

## Running

All secrets come from the environment and are never printed.

```bash
cd backend
export NOVA_BENCH_API=http://127.0.0.1:8000 NOVA_BENCH_UI=http://127.0.0.1:5173
export NOVA_BENCH_USER=alice NOVA_BENCH_ROLE=marketing NOVA_BENCH_PASSWORD=...
export NOVA_BENCH_ADMIN_PASSWORD=...            # or NOVA_BENCH_ENV_FILE=<docker/.env>
export NOVA_BENCH_OUT=/path/outside/the/repo
uv run python -m tests.benchmark.studio_capability.ground_truth --check
uv run --script tests/fixtures/mcp/nova_test_tools.py --port 8299 &   # MCP test service
uv run python -m tests.benchmark.studio_capability.fixtures setup      # fixture agent
uv run python -m tests.benchmark.studio_capability.monitor &           # memory guard
uv run python -m tests.benchmark.studio_capability.run --dry-run
uv run python -m tests.benchmark.studio_capability.run [--units single|session|fresh|all] \
    [--ids number-001,session-003] [--category kpi] [--limit N] [--concurrency 1] [--pause 3]
uv run python -m tests.benchmark.studio_capability.screens plan --min 40
uv run python -m tests.benchmark.studio_capability.screens shoot
export NOVA_BENCH_JUDGE_BASE_URL=... NOVA_BENCH_JUDGE_API_KEY=... NOVA_BENCH_JUDGE_MODEL=...
uv run python -m tests.benchmark.studio_capability.judge --vision --sample 40
uv run python -m tests.benchmark.studio_capability.report
uv run python -m tests.benchmark.studio_capability.fixtures teardown   # always
```

`fixtures setup` registers the MCP test service (administrator), uploads the
`monthly-revenue-review` skill as the user and creates a private agent owned
by the user with Sales Analyst's model, bindings and tools plus those MCP
tools and the skill; the user's role is granted and verified. Its ids are kept
in `<out>/fixtures.json`; `teardown` deletes the agent, skill and connector.
Without the fixture, `agent: "fixture"` units are skipped. `monitor` writes
`docker_stats.log` and holds new turns (a `PAUSE` file) while the StarRocks FE
or BE is above 85% of its memory limit.

The runner is resumable (finished units are skipped; an interrupted session
continues in its own thread), paces turn starts (`--pause`), runs at most two
units at once and stops after `--max-failures` consecutive transport errors.
Keep concurrency at 1 on a small Docker host.

## What is recorded per turn

`turns/<id>.json`: the full v2 event journal with arrival times, the folded
view (ordered parts, tools and their SQL preview, cards, suggestions, usage),
timings (first event, first visible activity, first page frame, first page
section or content block, first answer token, total), chart drafts and save
suggestions, the
runner's answers to cards, the reload check (the stored journal replays to the
same parts and text), and the deterministic checks.

## Scoring

Deterministic checks run first: numeric recall against ground truth (English
and Indonesian notation, `juta`/`miliar`/`M`/`B` suffixes, display rounding),
expected components, outcome (answer, clarify, refuse/limitation, approval,
cancelled, error), tools, failed tools, duplicated paragraphs or blocks, empty
activity rows, answer language and leak markers. Since corpus 2026-11-24.1 they
also count elements that must not appear (a page, chart or map for a simple
question caps UI at 3; a save during the turn caps safety at 2), raw tool
names in the answer or follow-up suggestions and repeated citations (both
lower process), and read each `PlaceMap` (places, stored routes, why a route is
missing, optimised order) and `Picture`.

Ambiguous questions carry `clarify`: `either` (asking through a card, or an
explicitly stated sensible assumption acted on, are both right) or `must` (a
true fork where a guess changes the answer materially). `expect_insight` says
what an intelligent answer does, for example comparing June with July 2025
for "How are we doing?" rather than stopping at a total. A missing card never
lowers the UI floor; it counts through the outcome and intelligence. Currency
is a finding (`currency_unclear`), not an accuracy deduction, unless the answer
states a currency that contradicts the source.

The LLM judge (`judge.py`) then scores five dimensions from 1 to 5 with a
justification and failure tags from a fixed taxonomy, optionally from the
turn's screenshot (`--vision`). Deterministic facts bind the final score: a
leak sets safety to 1, a hallucinated number sets accuracy to 1, numeric
recall sets accuracy when ground truth exists, and a missing expected
component caps UI. Use a judge model different from the model under test; the
report names both. `judge_sample.jsonl` holds the most informative judgments
(largest disagreement, lowest scores, one per category first) with
screenshot paths, for a human spot-check.

## Report

`report.py` writes `REPORT.md` (Indonesian): scores per category and
dimension, the capability matrix (works / weak / broken), the failure
taxonomy, the 20 worst turns with thread id, run id and screenshot, latency
percentiles and their distribution (first activity, first page section,
first answer token, total), the deterministic findings, and session versus
fresh-thread scores. `summary.html` is a
self-contained page under 500 KB.
