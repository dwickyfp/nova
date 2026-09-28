# Architecture: Agentic Harness (NOVA-124)

> One bounded agent loop serves Nove and every Studio agent; this is its design, its limits, and what it costs.

---

## Architecture

```text
user turn
  → route          fast path (Studio, fully resolved metric question) or model planner
  → context        system prompt + scope + curated history (ContextManager)
  → model action   tool calls or text
  → validate       allowed tool, arguments, consent (fail-closed)
  → execute        read-only calls may run in parallel (at most 3), writes are serial
  → verify         evidence tracker, business-evidence gate, numeric answer check
  → compose        verified prose, annotated prose, or a rendering from result cells
  → done           one finish reason, audit log
```

`app/modules/assistant/` is the engine: `service.py` (the loop), `planning.py`
(turn routing), `context.py` (history budget), `answer_contract.py` (numeric
verification), `intelligence.py` (evidence and turn states). Studio composes the
engine from `app/modules/agents/`: the registry chooses tools, `prompt.py`
builds the system prompt, and `semantic/` grounds data questions in a Semantic
View. Studio never forks the loop.

## Implementation

### Bounds

Every turn has an iteration cap, a wall-clock budget, a per-tool call cap, and a
context token budget. Studio agents choose a profile; `agents/service.py`
clamps it.

| Profile | Iterations | Time | Calls per tool | Where |
|---|---|---|---|---|
| `fast` | 8 | 60 s | 2 | Interactive |
| `analyst` (default) | 16 | 180 s | 6 | Interactive |
| `deep` | 40 | 30 min | 10 | Deep research worker only |

An interactive turn that asks for `deep` runs as `analyst`. When two steps or
fifteen seconds remain, the loop stops calling tools and composes the answer
from the evidence it has.

Repairs are counted per error class, so one bad argument does not use up the
budget for a missing required tool:

| Class | Repairs |
|---|---|
| `missing_required` | 1 |
| `composer_tool_call` | 1 |
| `business_evidence` | 2 |
| `tool_not_allowed` | 2 |
| `invalid_args` | 2 |
| `recoverable_tool` | 2 |

A declined consent becomes a tool result the model can answer around; a second
decline ends the turn.

### Context

`ContextManager` pins the system prompt and the recent window, clears old tool
results, and folds the oldest turns into a bounded note. The token budget is 60%
of the model's context window, capped at 120,000 tokens, unless the caller set
one explicitly. A dropped span is summarized in the background by the model and
cached per thread; until the summary exists, the deterministic note is used. A
turn that still does not fit ends with `context_overflow`, never a provider
error.

### Studio data questions

A Studio agent answers business questions only from governed results.

1. Routing. When the lexical planner maps every word of the question to the
   bound Semantic View with high confidence, and the question does not ask why,
   for a forecast, for documents, or for an automation, the turn is routed to
   `semantic_query` without a planner call. Otherwise the model planner routes
   it. If the planner fails twice, a safe plan is used: the governed query for a
   recognizable metric question, a clarification for anything else.
2. Planning the query. `semantic_query` uses the lexical plan when it is
   confident, and otherwise asks the model for a structured `SemanticPlan`
   constrained to the catalog. Nova validates the plan and compiles the SQL; the
   model never writes SQL on this path. An unresolved concept becomes a
   clarification.
3. The user's words win. The loop model often rewrites the question it passes to
   the tool. On the turn's first query, a period, a top N, or a threshold the
   user stated ("bulan ini vs bulan lalu", "Top 2 kanal", "di atas 1 miliar")
   replaces what the rewrite dropped or changed. Later queries in the same turn
   are analysis steps and keep their own parameters.
4. Metrics from different facts. The scope lists dimensions shared across facts
   (`sales_channel` and `marketing_channel`), and the tool asks for every metric
   in one question, so Nova compiles one drill-across query instead of two
   unrelated tables.
5. Evidence. A turn that answers a business question needs a governed result
   table. If the model answers without one, the loop runs `semantic_query` with
   the user's own words. If the governed tool ran and the request is outside the
   catalog, an answer that states the limit and contains no numbers ends with
   `out_of_scope`.
6. Verification. Every number in the answer must be a result cell, simple
   arithmetic over cells of one column (difference, percent change, share,
   total, average), a count of the result (rows, groups, rows per group), or a
   value from the question. Dates, list positions, and period lengths are not
   claims. `compute_metrics` returns percentages with a `%` sign so a model does
   not read -0.71% as a fraction. When a few numbers fail, only those are marked.
   When the headline number fails, the answer is rebuilt from the result cells
   in the user's language, with the values.

### Finish reasons

`stop`, `clarification`, `out_of_scope`, `denied`, `cancelled`, `timeout`,
`iteration_cap`, `context_overflow`, `consent_timeout`, `client_action_pending`,
`required_capability_unavailable`, `required_capability_incomplete`,
`data_evidence_incomplete`, `planning_failed` (Nove only), `unexpected_tool_call`,
`error`.

## Integration Points

- `tests/eval/` asserts behaviour by trajectory with a scripted provider: tool
  selection, consent, redaction, repair, and the finish reason. Every new tool,
  guard, or termination path gets a scenario there. Run
  `uv run python -m tests.eval.report` for the scorecard.
- `tests/benchmark/test_assistant_loop.py` measures loop overhead with a fake
  provider and a fake tool.
- `tests/benchmark/studio_accuracy/` measures answer accuracy at four levels;
  see [the benchmark guide](studio-accuracy/README.md).
- A new tool inherits fail-closed consent and must be added to
  `agents/tool_catalog.py` to be selectable.

## Configuration

| Setting | Where | Default |
|---|---|---|
| `budget_profile` | Agent configuration, "Analysis depth" | `analyst` |
| `budget_seconds` | Agent configuration | Profile value |
| `budget_tokens` | Agent configuration | 60% of the model window, at most 120,000 |
| `NOVA_MEMORY_EMBEDDING_ALIAS` | Environment | Unset: lexical memory retrieval |

## Measured cost

Loop overhead, fake provider and fake tool, median of several hundred runs on a
developer laptop (2026-09-28):

| Path | Median | p95 |
|---|---|---|
| Text-only turn | 0.22 ms | 0.26 ms |
| Text-only turn, 10 prior turns | 0.27 ms | 0.29 ms |
| Tool round trip with consent | 0.50 ms | 0.54 ms |
| Message assembly, 50 prior turns | 0.13 ms | 0.14 ms |
| Context curation, 50 prior turns | 0.09 ms | 0.09 ms |
| Iteration cap termination | 0.73 ms | 0.79 ms |

The loop itself costs well under a millisecond per turn. A live turn's time goes
to the model and the engine; the L3 results below break it down.

## L3 results

Full corpus, 157 English and Indonesian cases, each run twice against the dev
StarRocks with the configured model (2026-09-28, commit `6b0bb9a`; details in
`studio-accuracy/l3-baseline.json`):

| Run | Accuracy | Silent wrong | Consistency | p50 |
|---|---|---|---|---|
| Before the answer and planning fixes (3 per category, twice) | 82.9% | 11 | 75.6% | 9.1 s |
| After the fixes, before the last three planner fixes | 95.5% | 13 | 93.6% | 6.2 s |
| `6b0bb9a` | 99.7% | 0 | 99.4% | 7.3 s |

The one remaining failure is a clarification the model asked for once out of
two runs. The last row's p50 and p95 (47 s) are inflated: targeted runs shared
the provider while it ran. The planner fast path cut the time before the first
model call from 1.9 s to 0.4-0.6 s.

What moved the numbers, in order of effect:

- The user's own period, top N, threshold, and grain win over the loop model's
  rewrite on the turn's first successful query. Most wrong answers were correct
  SQL for a question the user did not ask.
- A LIMIT without ORDER BY now ranks by the first metric; before, it returned
  arbitrary rows.
- The verifier accepts list positions, worded percentages, result counts, and
  period-labelled direction, and rebuilds a rejected answer as sentences with
  values instead of a bare table.
- Out-of-catalog requests end with `out_of_scope` instead of an error.

These results are English and Indonesian only. The lexical parts of the
planner and verifier read those two languages; replacing them with a
language-neutral intent frame is the next step, measured on the eleven
languages in `studio-accuracy/cases_multilingual.yaml`.
