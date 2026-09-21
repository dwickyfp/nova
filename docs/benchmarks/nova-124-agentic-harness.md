# Nova agentic harness — context management, evaluation, and benchmark (NOVA-124)

> Deep research into the agentic harness, the improvements it produced, the eval
> suite that gates behaviour, and the measured cost of the new context manager.
> Read "How to read this" before quoting any number.

- **Date:** 2026-09-20
- **Python:** 3.11 (backend venv)
- **Machine:** Apple M3, macOS (arm64)
- **Scope of this change:** the Phase 10 bounded assistant loop
  (`app/modules/assistant`) and the Phase 12 Agent Studio layer
  (`app/modules/agents`) that composes it.
- **Reproduce:**
  ```bash
  cd backend
  uv run pytest tests/unit/test_assistant_context.py -q   # context unit tests
  uv run pytest tests/eval -q                             # behaviour eval
  uv run python -m tests.eval.report                      # eval scorecard
  uv run pytest tests/benchmark -q -s -m "not engine"    # offline benchmarks
  ```

## Research summary

The harness was assessed against current agent-engineering guidance:

- Anthropic, *Building effective agents* — agents are "LLMs using tools in a loop";
  keep the design simple, bound it, show the plan.
- Anthropic, *Effective context engineering for AI agents* — context is a finite
  resource; use **compaction**, **tool-result clearing**, and a **recency window**
  to keep the smallest high-signal token set.
- Anthropic, *Writing tools for agents* — tool definitions are prompt engineering;
  self-contained, unambiguous, poka-yoke'd.

Nova's loop already follows the first and third well. It was weakest on the
second: the loop replayed the **entire** transcript on every turn with no
windowing, no pruning, and no summarization. A long conversation would eventually
exceed the model's context window and fail with an opaque provider error, and
even sooner it would suffer context rot. That was the highest-value gap found and
is what this change closes.

### What the review found (ranked)

| # | Finding | Where | Status |
|---|---------|-------|--------|
| 1 | No context-window management: full transcript replayed unbounded | `service._build_messages` | **Fixed here** |
| 2 | `budget_tokens` stored but never enforced | `agents/repository.py` | **Wired here** |
| 3 | Benchmark "turn" cases measured a provider-resolution failure, not a real turn | `tests/benchmark/harness.py` | **Fixed here** |
| 4 | No agent behaviour/quality eval — only loop-overhead microbenchmarks | — | **Added here** |
| 5 | No provider retry/backoff; a transient 429/5xx aborts the turn | `assistant/provider.py` | **Fixed here** |
| 6 | Tool-bearing answer text is buffered until response composition; pure text preserves provider fragments | `service.run` | Fixed for ordered output |
| 7 | Parallel tool calls beyond the first silently dropped | `service.run` | **Fixed: serialized in declared order** |
| 8 | Token estimate is `len // 4`, not a tokenizer | `skills.py`, `context.py` | Open |
| 9 | Thread store / consent broker are process-local (single-worker) | `state.py`, `consent.py` | Open (documented) |
| 10 | Scope boundary enforced by prompt only, not code | `service._DEFAULT_SYSTEM_PROMPT` | Open (deliberate) |

Items 8-10 remain separate workstreams; several are deliberate v1 design
decisions (see `docs/specs/`).

NOVA Studio now adds trajectory gates for ordered text/table output, intentional
artifact-first output, SQL visibility during tool execution, monotonic run
envelopes, and persistence of ordered content indexes. These gates run with the
same scripted provider and require no provider credential.

## What changed

### 1. Context-window management (`app/modules/assistant/context.py`)

A pure, deterministic `ContextManager` that curates a provider message list to a
token budget. Order of operations, cheapest and least destructive first:

1. **Clear old tool results.** A tool result older than `tool_result_ttl`
   messages has its body replaced by a one-line marker. The message and its
   position survive; only the payload goes. This is Anthropic's "tool result
   clearing", the lightest-touch compaction.
2. **Drop oldest turns.** While still over budget, the oldest user/assistant
   exchange is removed and its text folded into a bounded summary note placed
   after the system prompt. Deterministic and bounded, so pruning never depends
   on a working provider.
3. **Report.** If the recent window alone still exceeds the budget, `fits` is
   false. The manager never removes the current turn or the system prompt.

The loop wires it in at `AssistantLoop._build_messages`; the stats are written to
`LoopContext.context_stats` and surfaced as a `thinking` note, and a persisted
`{"kind": "context", ...}` trace step in Agent Studio, so a shrunken transcript
is never silent. An over-budget pinned window now yields a clear
`context_overflow` frame instead of a provider error.

### 2. Per-agent context budget (`app/modules/agents/service.py`)

The existing but unused `budget_tokens` column is now read, clamped to
`[1_000, 120_000]`, and passed to the loop as a `ContextManager` budget. An unset
or non-positive value yields `None` and the loop default, so agents created
before this change behave exactly as before.

### 3. Behaviour eval suite (`backend/tests/eval/`)

A deterministic, offline evaluation of the harness's **behaviour** (as opposed to
the benchmark's **overhead**). A scripted provider stands in for the model, so
the suite runs in CI with no key and no engine. Thirteen golden scenarios, 37
checks, covering:

- tool selection, and no tool when none is needed;
- destructive calls prompt even under a read-only grant; read-only calls
  auto-approve under a grant and prompt without one;
- a denied call is reported and the turn continues; a failed call terminates and
  is not retried;
- iteration cap and time budget stop a runaway loop;
- credential-shaped tool arguments never reach the trace;
- a duplicate identical call is not re-run;
- a long transcript is curated and the curation is surfaced;
- an over-budget turn stops with `context_overflow` and makes **zero** provider
  calls.

Run `uv run python -m tests.eval.report` for a scorecard (non-zero exit on
failure); `pytest tests/eval` is the CI gate.

### 4. Benchmark harness fix (`tests/benchmark/harness.py`)

The scripted provider lacked `resolve(**kwargs)` and `stream(...)`, so every
`*_turn` benchmark actually terminated at the provider-resolution error path. It
now implements both, so the turn benchmarks measure a real loop iteration. This
is a correction to previously reported numbers, not a regression: the old
`text_only_turn` median of 7.2 µs measured the failure path.

## Measured results — loop overhead, offline

Fake provider + fake tool, `time.perf_counter_ns`, min/median/p95 in µs. Numbers
are from this session; ratios between cases are the durable result.

| Case | Iterations | min | median | p95 |
|---|---:|---:|---:|---:|
| `text_only_turn` (real turn now) | 500 | 16.7 | 17.1 | 18.3 |
| `text_only_turn_history_10` | 300 | 31.9 | 32.6 | 40.3 |
| `tool_round_trip` | 500 | 17.0 | 17.4 | 17.9 |
| `consent_auto_approve` | 500 | 15.8 | 16.2 | 18.5 |
| `consent_explicit_per_call` | 500 | 17.1 | 17.5 | 20.4 |
| `build_messages_history_0` | 1000 | 3.0 | 3.3 | 4.0 |
| `build_messages_history_10` | 1000 | 16.4 | 18.1 | 20.7 |
| `build_messages_history_50` | 1000 | 77.1 | 79.1 | 87.7 |
| `context_curate_noop_history_50` | 1000 | 40.9 | 41.9 | 46.4 |
| `context_curate_active_history_50` | 1000 | 40.8 | 41.7 | 44.7 |
| `iteration_cap_termination` | 300 | 68.3 | 70.3 | 78.8 |
| `time_budget_termination` | 500 | 13.7 | 14.1 | 17.0 |
| `denied_call_termination` | 300 | 17.2 | 17.5 | 18.1 |

**Cost of context management.** At 50 turns, curating with an active budget
(41.7 µs median) is within noise of a no-op walk (41.9 µs): the manager's cost is
the token estimate over the transcript, which the loop already paid to build the
messages. It is **single-digit microseconds**, whereas the real StarRocks
round-trip for a trivial `SELECT 1` was previously measured at
~206,000-296,000 µs (see `docs/benchmarks/nova-61-assistant.md`). Curation cannot
move user-visible latency.

## How to read this

1. **The context management fix is about correctness, not speed.** It prevents a
   long conversation from eventually overflowing the model window and stops the
   turn with a clear reason instead of an opaque provider error. The measured
   overhead is negligible.
2. **The loop is still not the bottleneck.** Provider and engine round-trips
   dominate. Every number here is the loop's own overhead with a fake provider
   and a fake tool.
3. **The eval suite is the quality gate.** Microbenchmarks cannot catch a
   regression where, say, a destructive call stops prompting. The 20 scenarios
   can. A change that breaks the harness's behaviour fails `tests/eval`.
4. **What is still open.** Tool-bearing response text is intentionally buffered
   for ordered composition, tools execute serially rather than in parallel, the
   token estimate is not a real tokenizer, and consent/thread state remains
   process-local. Pure-text answers stream provider fragments immediately;
   transient provider handshake/transport failures and 429/5xx responses retry
   with bounded backoff.
5. **The benchmark correction.** Turn-benchmark numbers in this document differ
   from the NOVA-92 report because that harness measured the provider-resolution
   error path. The engine figures quoted above are from the NOVA-92 report and
   remain the authoritative order of magnitude.
