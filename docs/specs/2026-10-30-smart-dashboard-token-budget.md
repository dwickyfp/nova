# Smart dashboard token budget

> **Date:** 2026-10-30
> **Status:** Phase 1 and Phase 2 (C7, C8a, C8b with F1, F2) implemented and measured
> on `deepseek-v4-1-flash` against a re-measured baseline (2026-10-31).
> Phase 3 open.
> **Scope:** Studio Smart (`backend/app/modules/assistant/service.py`,
> `backend/app/modules/agents/harness_worker.py`, `collaboration_tools.py`,
> `backend/app/modules/agents/tools/data_to_chart.py`)
> **Benchmark:** `backend/tests/benchmark/smart_tokens/`

## Goal

A Studio Smart request for a dashboard with two charts completes reliably and
spends far fewer tokens, without changing what the user gets: the same
governed numbers, charts that save to BI, the same authorization and evidence
checks.

Non-goals: changing the budgets' purpose (they stay hard limits), changing the
model provider, changing specialists' semantic planning, or adding a switch to
turn any of this off.

## How it is measured

`backend/tests/benchmark/smart_tokens/` runs the Smart worker with the provider
client wrapped (`probe.py`), asks a fixed request on a new thread (`run.py`), and
summarises each run (`report.py`). Every model call is recorded with the usage
the provider reported and an estimate of what its prompt was made of; no
message content is stored. See its README to repeat a measurement.

- Request: "Buatkan dashboard Field Sales Overview berisi chart jumlah kunjungan
  per sales rep dan chart jumlah outlet per kota", as `nova_admin`
  (`ACCOUNTADMIN`), on the local dev stack.
- Provider: kenari.id, default model `minimax-m3-1-flash`, about 40 s per
  model call. The default became `deepseek-v4-1-flash` on 2026-10-31, so a
  later phase measured on it is compared with a baseline re-measured on the
  same model, not with the numbers below.
- Code: `feat/superset-studio` at `1ae5033`.
- Five runs. Data: `docs/benchmarks/smart-tokens/2026-10-30-baseline.json`.

The comparison after each change uses the same request, five runs, and
`report.py --compare`. Means are over all runs, failed ones included, so a
change that only makes failures cheaper does not look like a saving.

## Baseline

| Measure (mean of 5 runs) | Baseline |
|---|---:|
| Completed runs | **1 of 5** |
| Wall time | 666 s |
| Model calls per request | 17 |
| Tokens per request, all calls | 182,071 |
| Root (Smart) calls | 6.2 |
| Root prompt tokens | 104,681 |
| Root prompt per call (mean / max) | 16,884 / 54,271 |
| Specialist calls / prompt tokens | 5.4 / 42,381 |
| One-shot calls (turn planner, semantic planner, chart intent) / prompt tokens | 5.2 / 19,267 |
| Tokens the harness stored per run tree | 135,417 |

Outcomes: one completed (567 s); three stopped by `BudgetExceeded`
(409-945 s); one stopped by `Session token budget exhausted` (474 s). In every
failure both specialists had already returned their data.

### Where the root's prompt goes

Estimated by part, summed over all root calls of a run (mean of 5 runs):

| Part | Tokens per run | Share |
|---|---:|---:|
| System messages | 46,460 | 52% |
| `wait_agent` results | 17,109 | 19% |
| Tool schemas | 8,471 | 10% |
| `spawn_agent` results | 6,696 | 8% |
| `discover_agents` result | 5,071 | 6% |
| Everything else (assistant, task, coordination, other tools) | 5,002 | 5% |

The system messages are sent again on every call. In the completed run they
were `<NOVA_PLATFORM>` (about 3,100 tokens), the claims instruction (315),
turn context (219), business scope (90), workspace and language (70), and,
on the final call only, `<FINAL_COMPOSER>` at about 11,200 tokens because it
carries every verified table as JSON.

Root decisions over the five runs: 10 `wait_agent`, 8 `spawn_agent` (two of
them in one call), 5 final answers, 3 `data_to_chart`, 2 `discover_agents`,
2 `followup_task`, 1 `list_agents`. Each `data_to_chart` result returned the
whole chart (about 1,100 tokens) to the model.

Specialists made 12 answer calls and 6 `semantic_query` calls, but also
4 `send_message`, 2 `data_to_chart`, 1 `dashboard_build` and 1 `compute_metrics`
that the delegated task did not ask for.

### Findings

1. **Calls multiply context.** A root call carries 11-25k tokens. The model
   spends whole calls on steps the harness can decide: whom to start (one
   specialist task per requested result), waiting, and charting a result whose
   shape already fixes the chart.
2. **Results the model does not need stay in context.** Wait results carry
   specialists' answers and evidence that the root also receives as structured
   evidence; chart results carry the full chart; the discovery catalog stays
   after the specialists are started.
3. **The final call is the largest.** `<FINAL_COMPOSER>` puts every verified
   table in the prompt, unbounded.
4. **Specialists do work nobody asked for**: charts, dashboards and messages
   to the root, each a call with a 3,600-token system prompt.
5. **Two budgets disagree.** Smart's collaboration limit is 240,000 tokens
   (`CollaborationLimits.max_total_tokens`), but `_execute_child` refuses to
   resume any participant once the tree has stored 120,000 (`MAX_SESSION_TOKENS`).
   The root (90,000 allowance) plus two specialists (60,000 each) can pass
   120,000 while every participant is within its own allowance; one baseline
   run failed this way after both specialists had finished. The stored totals
   also lag the provider's usage (root: 77k stored against 111k reported,
   prompt and completion), because they are written at checkpoints.

## Changes

Ordered by risk. Each is measured on its own before the next starts. The
"expected" figures are estimates from the baseline shares, to be replaced by
measurements.

### Phase 1: smaller context, same decisions (low risk)

**C1. Compact collaboration results for the model.** `wait_agent` and
`list_agents` results given to the model hold, per participant, its path,
status, a summary bounded to a few hundred characters and its collected
evidence ids with columns and row counts. Full answers and evidence stay
where they are now (imported as evidence, persisted in the run). Expected:
most of the 17k `wait_agent` tokens per run.

**C2. Compact `data_to_chart` results for the model.** The model gets the
title, mark, evidence id and row count; the chart itself still goes to the
answer as an artifact. Expected: about 1,000 tokens per chart call and per
later call that carries it.

*As built:* the chart was never in the result; its size was Evidence Health,
sent to the model four times per result (`data`, `metadata`, `evidence.health`
and `evidence.evidence_health`). Every tool result now carries it once, as
`evidence.health` (`_one_health_copy` in `tools/_boundary.py`); Nova keeps all
copies in the outcome, trace and evidence. This applies to every tool, not only
`data_to_chart`.

**C3. Tools by phase.** Once every requested specialist has started, the root
is offered only the tools that remain useful (`wait_agent`, `list_agents`,
`send_message`, `followup_task`, `compute_metrics`, `data_to_chart`);
discovery and spawning come back if a later step needs another specialist.
The discovery result is folded to a one-line summary after the spawns.
Expected: part of the 8.5k schema tokens and most of the 5k discovery tokens.

*Deferred:* measured, the three tools that would drop out (`discover_agents`,
`spawn_agent`, `propose_automation`) are about 400 of the root's 1,600 schema
tokens per call, and the context manager already clears the discovery result
after use. The saving does not justify changing which tools the model sees
mid-turn.

**C4. Bounded final composer evidence.** `<FINAL_COMPOSER>` carries each
verified table up to a row limit with its totals and row count, the same
bound the loop already uses for result previews. Number verification keeps
using the full evidence on Nova's side, never the trimmed copy. Expected:
most of the 11k-token final prompt.

*As built:* measured, the composer evidence held no tables: it listed every
evidence item, seven of eleven being coordination steps (discover, spawn,
wait) with their Evidence Health, about 2,400 tokens. `composer_context()` now
leaves coordination steps out once a result is collected; discovery stays
while none is, since it is what says nobody covers a request. The other 6,000
tokens of the final prompt are the interactive UI instructions, left to
Phase 2.

**C5. One session budget for Smart.** For a Smart tree, `_execute_child`
checks the tree against `CollaborationLimits.max_total_tokens`, the limit
Smart already declares, instead of `MAX_SESSION_TOKENS`. Non-Smart runs keep
`MAX_SESSION_TOKENS`. This is a correctness fix (finding 5), not a saving.

### Phase 2: deterministic dashboard steps (medium risk)

**C6. Start one specialist task per requested result without a model call.**
When the turn plan lists several requested results (a measure with its
grouping) and discovery finds their owner, the harness spawns one task per
result itself, with that result alone as the objective, as the guidance from
`18444dd` already asks the model to do. The model is called once results are
in. Expected: the `spawn_agent` calls (1.6 per run, 12-14k each).

**C7. Wait without the model until there is something to read.** While any
started specialist is still running and no message has arrived, the root waits
again on its own. `6e50cc0` already does this after a wait that timed out with
nothing new; this extends it to the first wait after the spawns. Expected: most of the 10 model-decided waits.

*As built (C7):* after a batch of spawns the harness waits for every task it
started, whether or not discovery named the measures' owners. Discovery found
no `semantic_matches` for this Indonesian request, so the old auto-wait, which
needed them, never ran. Spawned tasks are now tracked per path: two tasks for
one specialist used to overwrite each other, and only the last was waited for.

**C8. Chart a requested result directly.** When a requested chart's result has
one dimension and one measure, `data_to_chart` picks the mark from the column
types (date: line; category: bar) without its intent model call, and the
harness charts each requested result once the results are in. Results of
other shapes keep the model call. Expected: the root's `data_to_chart`
decisions and their one-shot intent calls.

*As built:* the harness does not chart results itself. Its only title source
would be a specialist's summary (long, full of numbers), while the model titles
charts well and already charts both results in one call. Instead:

- **C8a** (active): `data_to_chart` skips its intent model call when the result
  has one dimension and one measure and the request names no other chart type
  (`_shape_decides`); ShowMe's pick is used.
- **C8b** (inactive in practice): once every teammate is done and every result
  with at least two rows is charted, the root is sent straight to the final
  answer, without tools. It is gated on the turn plan requiring
  `data_to_chart`, and Smart's planner only required `discover_agents` for
  this request, so it never fired. Opening the gate (for example on the
  request asking for charts or a dashboard) is the open decision for Phase 2:
  without a gate, a request for a chart and a computation could be answered
  before the computation.

### Phase 3: specialists (medium risk)

**C9. Data-only delegated tasks get data tools.** A specialist whose delegated
objective asks for data is not offered `data_to_chart`, `dashboard_build` or
`send_message`; the root charts and composes. Expected: the 7 unrequested
specialist calls and part of each specialist's schema tokens.

## Acceptance

Measured with the benchmark, five runs, against the baseline above:

| Measure | Baseline | Target |
|---|---:|---:|
| Completed runs | 1 / 5 | at least 4 / 5 |
| Root prompt tokens per request | 104,681 | at most 52,000 |
| Tokens per request, all calls | 182,071 | at most 110,000 |
| Wall time | 666 s | at most 450 s |

And, unchanged from today:

- the saved charts hold the same governed numbers (checked against the
  specialists' SQL results) and save to BI as live charts;
- every number in the answer is verified; refused, failed and clarification
  turns behave as their current tests require;
- backend unit suite, `tests.eval.report`, the affected `tests/eval`
  trajectories, Ruff on changed files and mypy pass, with new trajectories for
  C1-C9;
- no new flag or setting: the new behavior is the only behavior.

## Rollout and reporting

Phase 1 lands first and is measured; Phase 2 and 3 only if Phase 1 does not
reach the targets. Each phase records its run in
`docs/benchmarks/smart-tokens/<date>-<phase>.json` and the comparison table in
this document.

## Results

| Measure (mean of 5 runs) | Baseline | Phase 1 | Phase 2 | Phase 3 |
|---|---:|---:|---:|---:|
| Completed runs | 1 / 5 | **4 / 5** | | |
| Root prompt tokens | 104,681 | 73,750 (−30%) | | |
| Tokens per request | 182,071 | 153,998 (−15%) | | |
| Wall time (s) | 666 | **288 (−57%)** | | |
| Root calls | 6.2 | 8.0 | | |
| Specialist prompt tokens | 42,381 | 27,865 (−34%) | | |
| One-shot prompt tokens | 19,267 | 32,485 (+69%) | | |

Phase 2 is not in the table: its five runs are not a valid measurement.
Runs 4 and 5 failed on the provider's HTTP 402 (credit exhausted), and the
provider answered 3-10 times slower than during Phase 1 (single calls of
95-157 s), so wall time and the time-budget failure of run 3 say nothing about
the change. The two runs that completed used 47k and 76k root prompt tokens
(5 and 8 root calls); data, with these caveats, in
`docs/benchmarks/smart-tokens/2026-10-30-phase2-partial.json`. Phase 2 is
measured again once the provider has credit.

Run 3 also showed the time budget behaving like the token budget did before
`18444dd`: the answer was composed, and `before_final`'s wall-time check
discarded it. That is a candidate fix alongside C8b.

Phase 1 data: `docs/benchmarks/smart-tokens/2026-10-30-phase1.json`.

### On `deepseek-v4-1-flash` (2026-10-31)

The default model changed, so the baseline was measured again on the new
model: baseline code (`fd69f54`, run from a detached checkout) against the
current code (`d9de539`: Phase 1, C7 and C8a), five runs each, same request.

| Measure (mean of 5 runs) | Baseline | Current | Change |
|---|---:|---:|---:|
| Completed runs | 2 / 5 | 3 / 5 | |
| Wall time (s) | 185 | 169 | −9% |
| Tokens per request | 162,983 | 136,881 | −16% |
| Root prompt tokens | 88,199 | 81,489 | −8% |
| Root calls | 6.0 | 7.6 | +27% |
| Root prompt per call | 14,700 | 10,722 | −27% |
| Specialist prompt tokens | 26,862 | 16,331 | −39% |
| One-shot prompt tokens | 35,352 | 28,848 | −18% |

Data: `docs/benchmarks/smart-tokens/2026-10-31-deepseek-baseline.json` and
`2026-10-31-deepseek-phase1+2part.json`. All failures on both sides were token
budgets (`BudgetExceeded`, `token_budget`), none were provider errors.

This model is about three times faster than the Phase 1 measurement, so wall
time is no longer the constraint; the root's token allowance is. Every call is
smaller, but the final answer now takes more calls:

- **Answer calls per run: 1.4 before, 2.8 now** (about 31k and 39k prompt
  tokens). The sequence is a draft answer (about 9k), the interactive composer
  call (about 15k), then one or two more (about 18k and 24k). Which check sends
  the answer back (number verification, the UI program's validation, or both)
  is not yet established; it is the first thing to find before more changes.
- **C8b never fired** (no `needs_chart` from Smart's planner), so the draft
  answer and the unrequested `compute_metrics` calls (in 2 of 5 runs) remain.
- System messages are 62% of the root's prompt (about 46k per run): fewer root
  calls is still the main lever.

Next, in this order: find what makes the final answer take 2-4 calls; open
C8b's gate (proposed: the request asks for charts or a dashboard); stop the
wall-time check discarding a composed answer, as `18444dd` did for tokens.

### Why the final answer takes 2-4 calls (investigated 2026-10-31)

From the five current-code runs' checkpoints (`repairs`, and the messages
appended after the last tool result):

| Run | Answer calls | What happened |
|---|---:|---|
| f6fda094, e18cb75f | 2 | draft, then interactive composition |
| 9de80e1e, 15ca05fc | 3 | draft, composition, number rewrite |
| c1b535b3 | 4 | draft, composition, number rewrite, composition again |

1. **The first answer is always a throwaway draft.** When the model stops
   calling tools in an interactive turn, the loop appends `<FINAL_COMPOSER>`
   (about 8,300 tokens) and asks again (`service.py`, the
   `context.ui_prompt and turn_plan.presentation == "interactive"` branch).
   Every run pays one draft call of about 9k tokens.
2. **A number rewrite costs two calls and a second composer copy.** The
   `unverified_number` repair sets `composing_final = False` so the model may
   call `compute_metrics`; when it answers again, the interactive branch
   appends `<FINAL_COMPOSER>` a second time. Run c1b535b3 carries two copies,
   about 16,500 tokens, in its last call.
3. **What the rewrites flagged were real values, phrased loosely.** The
   flagged numbers (40, 80, 1,053, 1,060) are cells of the collected tables,
   which were present (no table was evicted). Offline, "Jakarta Selatan 80
   outlet, kota lain masing-masing 40" fails without a claims block because 40
   is attributed to the last named row, and passes with a claim that names the
   column. The verifier behaves as designed; it is not loosened.

Fixes, proposed:

- **F1 (C8b's gate):** when every teammate is done, every result with at
  least two rows is charted, and the request asks for charts or a dashboard,
  go straight to composition. Removes the draft call (about 9k per run).
- **F2:** send `<FINAL_COMPOSER>` once per turn; when composition starts
  again after a repair, a short system line points back to it. Saves about
  8k tokens in every run that repairs (3 of 5).
- Number rewrites themselves stay: they are the answer contract working.

*As built:* F1 opens C8b when the request names charts, a dashboard, a
diagram, a plot or visualisation (`_asks_for_charts`, English and
Indonesian), in addition to a plan that requires charts. F2 routes every
composer message through `composer_message()`: the full composer is sent
once per turn and again only when new evidence arrived since; otherwise a
one-line pointer.

### Phase 2 result on `deepseek-v4-1-flash` (2026-10-31)

Five runs, same request; data in
`docs/benchmarks/smart-tokens/2026-10-31-deepseek-phase2.json`.

| Measure (mean of 5 runs) | Baseline | Phase 1 + C7 + C8a | Phase 2 (F1, F2) | Target |
|---|---:|---:|---:|---:|
| Completed runs | 2 / 5 | 3 / 5 | **4 / 5** | ≥ 4 / 5 ✅ |
| Root prompt tokens | 88,199 | 81,489 | **55,665 (−37%)** | ≤ 52,000 |
| Tokens per request | 162,983 | 136,881 | **123,993 (−24%)** | ≤ 110,000 |
| Root calls | 6.0 | 7.6 | 5.6 | |
| Specialist prompt tokens | 26,862 | 16,331 | 20,567 | |
| Wall time (s) | 185 | 169 | 200 | ≤ 450 ✅ |

The four completed runs used 44,432 root prompt tokens and 96,114 tokens per
request on average, both within the targets; the means above include the one
failed run (`BudgetExceeded`), in which the root traded `followup_task` and
`list_agents` with a specialist six times before answering.

A completed run is now spawn, wait, chart, answer: one model call each, with
a single 15k-token answer call (it was a draft plus one to three
compositions). What is left, for Phase 3:

- The root still makes one model-decided `wait_agent` call (about 8k) after
  the automatic wait, when a specialist's message wakes it (C9 removes
  specialists' `send_message`).
- The failed run's follow-up loop between root and specialist.
- One-shot calls (turn planner, semantic planner) are unchanged at about 36k
  per request; the turn planner runs again for each specialist turn. Code:
C1, C2 (as built), C4 (as built) and C5; C3 deferred.

Phase 1 meets the completion and wall-time targets, not the token targets.
Read with care: baseline runs mostly failed early, so they skipped work that
completed runs do (charts, the final answer, more specialist turns). That is
why root calls and one-shot tokens rose while each call got smaller: root
prompt per call fell from 16,884 to 9,219.

The one failed run stopped at the root's own token allowance
(`token_budget`): the root's tools' nested model calls (chart intent) count
toward it.

What Phase 2 has to address, from the Phase 1 measurement:

- System messages are 56% of the root's prompt (38,946 tokens per run), sent
  on each of its 8 calls: fewer root calls is the largest remaining lever
  (C6-C8).
- Tool schemas are 16% (11,403 per run) because there are more calls.
- The turn planner (`nova_turn_plan`) ran 22 times in 5 runs, about 5,700
  prompt tokens each: every participant's turn plans again, including
  specialists whose objective Smart already wrote.
- The interactive UI instructions are about 6,000 tokens of the final call.
