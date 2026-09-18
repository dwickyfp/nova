# Nova Assistant — measured benchmark (NOVA-61 Phase 10, NOVA-92)

> Performance numbers for the bounded agentic assistant loop, with the exact
> command to reproduce them. Read the "How to read this" section before quoting
> any number: this harness measures the **loop's own overhead**, not end-to-end
> latency.

- **Date:** 2026-09-18
- **Measured at revision:** the head of this PR branch, whose only benchmark
  commits are the two introducing `backend/tests/benchmark/` (base
  `0ca873e` → head). The SHAs move on every rebase, so this report names no
  fixed SHA; the durable citation once the PR lands is the merge commit on
  `main`. **Verify the checkout actually contains the suite before running the
  reproduce block:**

  ```bash
  git rev-parse HEAD                                  # the revision you are about to measure at
  git ls-tree -r --name-only HEAD | grep tests/benchmark/   # must print the harness + tests
  ```

  Run from the repository root. If the second command prints nothing, the
  checkout predates the benchmark and the reproduce command would fail with
  `file or directory not found` — check out this PR's head instead.
- **Python:** 3.12.8
- **Machine:** Apple M3, 8 cores, macOS 26.6.2 (arm64)
- **Method:** `time.perf_counter_ns`, N iterations per case, reported as
  min / median / p95 / max in microseconds. No `pytest-benchmark` dependency
  was added; the harness is standard library only.

## Reproduce

```bash
cd backend
uv run pytest tests/benchmark -q -s -m "not engine"   # offline, deterministic
uv run pytest tests/benchmark -q -s -m engine         # requires Docker stack
```

Each case prints one `BENCHMARK {…}` JSON line. Re-running twice yields the same
pass set; the timings vary within the spread recorded below.

## Loop overhead — offline, fake provider + fake tool

| Case | Iterations | min (µs) | median (µs) | p95 (µs) | max (µs) |
|---|---:|---:|---:|---:|---:|
| `text_only_turn` (1 iteration, no tool) | 500 | 7.0 | 7.2 | 7.7 | 239.5 |
| `text_only_turn_history_10` (20-message transcript) | 300 | 15.4 | 15.8 | 16.4 | 33.2 |
| `tool_round_trip` (propose → consent → run → fold → text) | 500 | 7.3 | 7.5 | 7.8 | 334.0 |
| `consent_auto_approve` (read-only grant, gate skipped) | 500 | 6.0 | 6.4 | 6.8 | 80.8 |
| `consent_explicit_per_call` (no grant) | 500 | 7.2 | 7.4 | 7.7 | 49.8 |
| `build_messages_history_0` | 1000 | 1.0 | 1.1 | 1.3 | 35.6 |
| `build_messages_history_10` | 1000 | 8.8 | 9.1 | 10.2 | 36.0 |
| `build_messages_history_50` | 1000 | 40.5 | 42.2 | 46.9 | 202.9 |
| `iteration_cap_termination` (cap = 8) | 300 | 47.8 | 49.2 | 92.3 | 226.1 |
| `time_budget_termination` (budget = 0 s) | 500 | 7.2 | 7.4 | 7.6 | 22.8 |
| `denied_call_termination` | 300 | 7.2 | 7.6 | 7.8 | 79.2 |

Context-assembly cost is linear in transcript length: ~1.1 µs empty, ~9.1 µs at
10 turns, ~42 µs at 50 turns — roughly 0.8 µs per stored message.

## Default skill prompt — size sent on every turn

| Metric | Value |
|---|---:|
| Characters | 5,696 |
| Estimated tokens | 1,424 |
| Token budget | 2,000 |
| Primer sections | 6 |
| Source revision hash | `6033133b2f51` |

The prompt is assembled once at import and cached, so its per-turn cost is zero
recomputation; the number that matters is its **token cost** against the model's
context window, not CPU time.

## Engine round-trip — real StarRocks via `query_execute` (skip-clean)

| Case | Iterations | min (µs) | median (µs) | p95 (µs) | max (µs) |
|---|---:|---:|---:|---:|---:|
| `engine_query_execute_round_trip` (`SELECT 1`) | 20 | 188,244 | 210,019 | 313,941 | 1,528,089 |
| `engine_query_execute_round_trip` (re-verified 2026-09-18, session A) | 20 | 193,817 | 295,797 | 744,266 | 1,683,518 |
| `engine_query_execute_round_trip` (re-verified 2026-09-18, session B) | 20 | 191,656 | 206,321 | 215,793 | 372,374 |

Measured on the `docker-compose.test.yml` stack, statement `SELECT 1 AS one`.
The two re-verified rows are back-to-back runs on the same host; session B's
median (206,321 µs) reproduces the original (210,019 µs) within ~2%, while
session A ran ~1.4× higher. That spread is the engine number's honest variance,
not a new constant: the case is `@pytest.mark.engine` and must be read as an
order of magnitude.

The test skips cleanly — never fails — when it cannot run: no Docker or no
compose file (module guard), no stack from **this checkout** publishing the port
(the container's `com.docker.compose.project.working_dir` label must match this
`backend/` directory, so a foreign project on the same port is ignored), or the
app system pool cannot be initialised (the audit path `db.execute_system` needs
`init_system_pool()`, which the test calls itself). `NOVA_ORCH_SR_PORT` /
`NOVA_ORCH_SR_HOST` override the port when another project already owns the
default. The harness provisions `NOVA_SYSTEM.AUDIT_LOG` through the integration
suite's idempotent helper, because `query_execute` audits every call.

**Reproduction note:** StarRocks FE refuses to start when the host has **less
than 5 GB free** for its metadata directory (`MetaHelper.checkMetaDir`). The
engine case therefore also skips when the stack cannot come up — it does not
fake a number. Run it on a machine with the test stack already healthy:

```bash
cd backend
docker compose -f docker-compose.test.yml up -d --wait
uv run pytest tests/benchmark -q -s -m engine
```

## Variance

- Microbenchmark spread (p95 / median) is **1.05–1.15×** for the stable cases.
  The `max` column is a scheduler artefact (GC, another process) and is not
  representative; use median/p95.
- The `tool_round_trip` max of 334 µs and `iteration_cap_termination` max of
  226 µs are single outliers in 500/300 runs, not a second mode.
- Engine numbers are far noisier by nature: p95/median = 1.50, max ≈ 7× median.
  Treat the engine figure as an order of magnitude, not a precise constant.
- Different machine class will move absolute numbers; the ratios between cases
  are the durable result.

## How to read this

1. **The loop is not the bottleneck.** Its own overhead is single-digit to
   low-double-digit microseconds per turn. The real StarRocks round-trip for a
   trivial `SELECT 1` is **~206,000–296,000 µs** (re-verified; the original
   session recorded ~210,000 µs) — on the order of **tens of thousands of
   times** the loop's median text-only turn (7.2 µs). Optimising the loop
   further cannot move user-visible latency.
2. **What the production cost is.** A real turn spends its time on (a) the LLM
   provider round-trip and (b) the StarRocks round-trip. This harness
   deliberately excludes both: the provider is a scripted fake, and only the
   engine-marked case touches StarRocks at all.
3. **What this does not cover.**
   - No real LLM latency, token streaming, or network variability.
   - No real StarRocks latency for non-trivial queries (the engine case is
     `SELECT 1`, a lower bound).
   - Single-process loop only: no HTTP transport, no SSE serialisation over a
     socket, no thread-store lock contention under concurrency.
   - Consent is resolved by an in-process coroutine, not the two-request
     round-trip (client decision over HTTP) that production uses.
4. **The decision this informs.** If loop overhead is not the bottleneck and the
   dominant cost is provider + engine round-trips, then the revisit triggers for
   sidecar v2 and E5b (cross-restart persistence) must be argued from
   provider/engine latency or product need — **not** from loop CPU, which is
   negligible by measurement.
