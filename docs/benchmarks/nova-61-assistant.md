# Nova-61 assistant — bounded loop benchmark

Measured performance for the bounded agentic assistant
(`backend/app/modules/assistant/`), Phase 10. This report exists so the
assistant's cost is a number, not an assertion.

- **Issue:** NOVA-92 (parent NOVA-61, "agentic assistant").
- **Date measured:** 2026-09-18.
- **Git revision:** `72a09fa14fb4c13b5c17def97d5368b5bc76c378` (`main` at checkout).
- **Machine class:** Apple M3, 8 cores, macOS 26.6.2 (25G83). CPython 3.11.13
  via `uv` (the project pins `requires-python = ">=3.11"`).

## Reproduce

Offline suite (deterministic, no network, no StarRocks — prints the tables
below to stdout):

```bash
cd backend
uv run pytest tests/benchmark -q -s
```

Optional engine number (needs the Compose stack; **skips** without it):

```bash
cd backend
docker compose -f docker-compose.test.yml up -d --wait
uv run pytest tests/benchmark -m engine -q -s
```

No new dependency is used. Timings come from `time.perf_counter_ns`
(monotonic, nanosecond resolution); statistics are nearest-rank p95 over 200
repetitions for the offline cases.

## Headline numbers — loop overhead per turn

Provider time **excluded** by construction (the fake provider returns a canned
message immediately). These measure only the loop's own work: context assembly,
control flow, consent handling, and SSE frame formatting.

| case | iters | min (ns) | median (ns) | p95 (ns) | median |
| --- | ---: | ---: | ---: | ---: | ---: |
| `text_only_1_iter` | 1 | 6,250 | 6,500 | 8,334 | 6.5 µs |
| `tool_round_trip_explicit` | 2 | 14,125 | 14,542 | 16,583 | 14.5 µs |
| `tool_round_trip_grant` | 2 | 11,709 | 12,167 | 13,000 | 12.2 µs |

- `text_only_1_iter` — one provider iteration, text answer, no tool call.
- `tool_round_trip_explicit` — propose → explicit consent → run → fold → final
  text (2 provider iterations).
- `tool_round_trip_grant` — same script with a conversation read-only grant
  (auto-approve path). It emits one fewer frame (no `tool_call`) and skips the
  awaited resolver, which is why it is ~2.3 µs cheaper at the median.

## Consent gate cost

The difference between the two tool rows is the cost of the gate: **≈2.3 µs at
the median** (14.5 µs explicit vs 12.2 µs auto-approved) on top of the
round-trip itself. The gate is a frame emission plus one `await` on an
already-resolved coroutine in this harness; a real transport would add the
client's decision round-trip, which is outside this measurement.

## Context assembly and prompt size

| item | value |
| --- | ---: |
| `_build_messages`, 0 history entries | 125 ns (median) |
| `_build_messages`, 50 history entries | 2,000 ns (median) |
| `DEFAULT_SKILL_PROMPT` size | 5,696 characters |
| `DEFAULT_SKILL_PROMPT` estimated tokens | 1,424 tokens |
| Assembled-skill metadata `tokens` | 1,284 tokens |

The system prompt is sent on every provider call, so its size is a fixed
per-turn cost. The estimate is the module's own deterministic estimator
(`_estimate_tokens`, 4 chars/token — `backend/app/modules/assistant/skills.py`),
not a real tokenizer; it is used so the number is reproducible without a
tokenizer dependency. `metadata.tokens` (1,284) is the value the skill's own
2,000-token budget enforces; the assembled string is slightly larger (1,424)
because it includes the excerpt delimiters and stripped/joined seed text.

## Bounded termination (measured, not hung)

| path | observed |
| --- | --- |
| iteration cap (`max_iterations=3`) | terminates in ~37 µs, 8 frames, last frame `done` / `iteration_cap` |
| wall-clock budget (`time_budget_seconds=0`) | terminates in ~22 µs, 2 frames, `error` / `timeout`; provider never called |

Both guards return rather than hold the worker. The budget test uses a zero
budget so it proves the deadline check fires before the provider call without
sleeping.

## Engine-integrated benchmark (optional)

`backend/tests/benchmark/test_engine_round_trip.py` measures a read-only
`SELECT 1` through `query_execute` → `QueryService.execute_statements` against
the real stack. It is marked `@pytest.mark.engine` and **skips cleanly** when
StarRocks is unreachable or the delegate-first user connection is unavailable —
it never fabricates a credential and never falls back to a service identity.

No engine number is published in this revision: the stack was not running in
this measuring session, so the engine case reports `skipped`. A measured engine
number requires the Compose stack; run the command above to produce it. The
harness is deliberately conservative — if the tool path cannot be driven with
the requesting user's connection, the test skips rather than inventing a
session.

## How to read this

**Loop overhead is tiny by construction.** A turn of the bounded loop costs
single-digit-to-low-tens of microseconds. That is the point: the loop is a small
state machine over an `async` provider interface, and these numbers bound its
non-I/O cost.

**The dominant production cost is excluded on purpose.** In production, turn
latency is dominated by (a) the LLM provider round-trip per iteration and
(b) the StarRocks engine round-trip inside `query_execute`. This harness
neither calls a provider nor opens a socket, so it cannot and does not represent
that latency. Do not read these numbers as end-to-end turn latency.

## Variance and limits

- **Variance:** the median is stable run-to-run at the low-microsecond level,
  but absolute numbers vary by machine, CPU frequency scaling, and Python build.
  Treat these as an order-of-magnitude baseline, not a contractual SLA. Re-run
  the command above on the target machine for a comparable figure.
- **What is not covered:**
  - real LLM provider latency (no network call);
  - real StarRocks engine latency (no socket) — the `@pytest.mark.engine` case
    addresses this but was skipped here;
  - multi-worker / multi-process behaviour (the loop is measured
    single-process);
  - client transport cost (the consent decision HTTP round-trip is not
    represented — the resolver resolves in-process);
  - concurrent turns (the loop is serialized per turn by design; contention
    across turns is not measured).
- **No credentials:** the fake provider uses the literal placeholder
  `"bench-key"` and never transmits it; the offline context carries no
  `encrypted_password`; the engine case derives its password from
  `NOVA_ORCH_SR_PASSWORD` (empty on the passwordless Compose root) and never
  commits it. No credential-shaped value appears in any fixture, output, or
  this report.
