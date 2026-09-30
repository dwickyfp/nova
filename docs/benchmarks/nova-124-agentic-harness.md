# Architecture: Agentic Harness (NOVA-124)

> One bounded agent loop serves Nove and every Studio agent; this is its design, its limits, and what it costs.

---

## Architecture

```text
user turn
  → plan           one structured call: route, intent frame, and (Studio) the first query plan
  → context        system prompt + scope + curated history (ContextManager)
  → model action   tool calls or text
  → validate       allowed tool, arguments, consent (fail-closed)
  → execute        read-only calls may run in parallel (at most 3), writes are serial
  → verify         evidence tracker, business-evidence gate, numeric answer check
  → compose        verified prose, annotated prose, or a rendering from result cells
  → done           one finish reason, audit log
```

`app/modules/assistant/` is the engine: `service.py` (the loop), `planning.py`
(turn routing), `intent.py` (the intent frame), `context.py` (history budget),
`answer_contract.py` (numeric verification), `locale_numbers.py` (numbers in any
language), `messages.py` (Nova's own text in the user's language),
`intelligence.py` (evidence and turn states). Studio composes the
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

No step reads the user's words with a regex or a word list. The model reads
the question in whatever language it is written; Nova checks what the model
returns against the catalog, the time grammar, and the result cells.
`tests/unit/test_no_language_regex.py` fails when a natural-language word
appears in a regex or a word set under `assistant/`, `agents/`, or
`intelligence/`.

1. Planning. One structured call returns the route, an intent frame, and, for
   a data question, the first `SemanticPlan` and the view it uses. The frame is
   language-neutral: the user's language (BCP-47), the range and comparison in
   the canonical time grammar (`previous_month`, `2025-Q2`), the grain, whether
   a series was asked for, top N, order, a per-group dimension, a threshold,
   whether the question refers to the screen, and whether it compares groups.
   Nova validates the plan and compiles the SQL; the model never writes SQL on
   this path. A valid plan is run before the first loop model call, so a simple
   data turn costs about two model calls. If planning fails twice, the turn runs
   `semantic_query` with the user's own words, or asks for clarification when no
   view is bound.
2. The catalog. The planner sees the whole authorized catalog when it fits the
   budget (6,000 tokens, `NOVA_SEMANTIC_CATALOG_TOKENS`). A larger catalog is
   narrowed with embeddings (`NOVA_SEMANTIC_EMBEDDING_ALIAS`), or truncated with
   a note in the trace when no embedding model is set. Nothing is chosen by
   shared words, so a question in Japanese sees the same catalog as one in
   English. A verified query is reused when its plan is identical.
3. The user's request wins. The loop model often rewrites the question it passes
   to the tool. On the turn's first successful query, the frame's period, grain,
   top N, per-group rank, order, and threshold replace what the rewrite dropped
   or changed. Later queries in the same turn are analysis steps and keep their
   own parameters.
4. Metrics from different facts. The scope lists dimensions shared across facts
   (`sales_channel` and `marketing_channel`), and the tool asks for every metric
   in one question, so Nova compiles one drill-across query instead of two
   unrelated tables.
5. Evidence. A turn that answers a business question needs a governed result
   table. If the model answers without one, the loop runs `semantic_query` with
   the user's own words. If the governed tool ran and the request is outside the
   catalog, an answer that states the limit and contains no numbers ends with
   `out_of_scope`.
6. Verification. On a data turn the model appends a `<claims>` block that Nova
   strips before the user sees the answer. Each claim names a number as written,
   its value, and its kind: a result cell, a derivation (difference, percent
   change, share, total, average, multiple), a count of the result, a number
   from the question, a list position, a date, or a comparison between rows.
   Every number in the answer must be backed by the cells. A claimed direction
   must match the sign of the change, and a claimed comparison ("Website is
   higher than Marketplace") must match the cells. Without claims, numbers are
   still checked by value, but direction and comparison are not.
7. Numbers in any language. Digits in any script are normalized. Decimal and
   group marks, compact scales (juta, Mio., 万, 億, 조), and currency symbols
   come from the Unicode CLDR data that Babel ships, not from hand-written
   lists. A unit word CLDR does not know is accepted only through a claim.
8. Nova's own text. Fallback renders, consent and no-rows messages, and
   follow-up suggestions come from a message catalog (English source,
   Indonesian built in). Other languages are translated once by the model,
   accepted only when every placeholder survives and no new digit appears, and
   cached in `NOVA_SYSTEM.CONFIG_I18N_MESSAGES`. When a failing number is the
   headline, the answer is rebuilt from the result cells in the user's language
   and number format.

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
| `NOVA_SEMANTIC_CATALOG_TOKENS` | Environment | 6000: the full catalog up to this size |
| `NOVA_SEMANTIC_EMBEDDING_ALIAS` | Environment | Unset: a larger catalog is truncated |

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
the provider while it ran.

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
