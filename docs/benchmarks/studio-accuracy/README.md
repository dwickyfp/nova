# Studio accuracy benchmark

> How accurately a Nova Studio agent answers business questions from a Semantic View, measured at four levels.

---

## Levels

| Level | What runs | Gate | Where |
|---|---|---|---|
| L0 offline | The lexical planner on every case, no engine, no model | CI on every PR | `tests/unit/test_studio_accuracy_l0.py` |
| L1 trajectory | The agent loop with a scripted provider: tool choice, consent, repair, finish reason | CI on every PR (`-m eval`) | `tests/eval/` |
| L2 engine | Each expected plan compiled by Nova and executed on StarRocks, compared with an independent gold query | Engine CI job | `run.py --engine` |
| L3 live | The real agent loop with the configured model, on StarRocks | Nightly or on request | `run.py --live` |

L0 and L2 check the compiler, the time grammar, and the planner fast path. They
do not involve a model, so their results are exact and repeatable. L3 measures
what a user sees. The model is not deterministic, so L3 runs each case more than
once and reports consistency next to accuracy.

## Data

`dataset.py` builds the `NOVA_BENCH` database with a seeded generator: orders,
order items, customers, products, marketing spend, and support tickets. Dates
are anchored to the day of the load, so "last quarter" and "7 hari terakhir"
always have rows. `--load` rebuilds only `NOVA_BENCH`. No other database is
touched.

`model.py` defines the `nova_bench` Semantic View with English and Indonesian
synonyms, named filters, and a conformed channel dimension (`sales_channel` on
orders, `marketing_channel` on marketing spend), so a question that combines
revenue and marketing spend compiles to one drill-across query.

`gold.py` computes expected rows with SQL written independently of the
compiler. Comparison periods follow the calendar convention: a period still in
progress is compared with the same number of elapsed days in the prior period.

## Cases

`cases.yaml` holds hand-written cases and `cases.py` adds generated time-range
cases. `cases_multilingual.yaml` holds 25 base questions in eleven more
languages (es, pt, fr, de, ja, zh, ko, ar, vi, th, hi), each expanding to
`ml.<base>.<lang>` with the base's expectation. Names are written the way a
speaker of the language writes them (ジャカルタ, Yakarta). Each case has a question, a language, a category, and either an expected
plan (`expect`) or an outcome (`clarify`, `refuse`). Derived-number cases also
carry an `answer` spec: the value a correct answer states (a percent change, a
difference, a share), computed from the gold rows.

## Metrics

`accuracy` is the share of cases that passed. For an answerable case, a result
table from the governed tool must match the gold rows (extra columns such as a
rank are allowed, and a ranked table passes when its first N rows are the gold
top N), or the answer text must state the expected derived value. The turn must
end with `stop`.

`silent_wrong` counts confident wrong answers: the turn ended normally with a
result table that does not match gold, or an out-of-scope question was answered
with numbers and no stated limitation. The gate is zero.

`languages` repeats accuracy and `silent_wrong` per language; a case's language
is the last part of its id.

`consistency` is the share of cases that passed in every repetition. `flaky`
lists the cases that passed in some repetitions and failed in others.

`p50_ms`, `p95_ms`, and `mean_timing_ms` break a turn into time before the loop
(routing and context), model time, model calls, and tool time.

A case whose provider or network call failed is skipped and reported, not
counted as a failure.

## Running

```bash
cd backend && uv run python -m tests.benchmark.studio_accuracy.run
```

```bash
cd backend && uv run python -m tests.benchmark.studio_accuracy.run --engine
```

```bash
cd backend && NOVA_LIVE_ASSISTANT_TEST=1 NOVA_BENCH_USER=<user> NOVA_BENCH_PASSWORD=<password> uv run python -m tests.benchmark.studio_accuracy.run --live --repeat 2 --json
```

L3 runs as a StarRocks user that can read `NOVA_BENCH`. Pass that account in
`NOVA_BENCH_USER` and `NOVA_BENCH_PASSWORD`; the runner never writes either value
to a file or a log. On the dev stack the StarRocks FE MySQL port is published on
the host as `29030`, so set `STARROCKS_FE_MYSQL_PORT=29030` when running from the
host.

Useful flags:

| Flag | Effect |
|---|---|
| `--case ID` | Run one case; repeatable |
| `--category NAME` | Run one category; repeatable |
| `--lang CODE` | Run one language; repeatable |
| `--per-category N` | The first N cases of every category |
| `--repeat N` | L3: run each case N times and report consistency |
| `--load` | Rebuild `NOVA_BENCH` first |
| `--baseline FILE` | Fail when accuracy drops more than 2 points below the file, or `silent_wrong` is above zero |
| `--write` | Write `docs/benchmarks/studio-accuracy/<date>.json` |

`NOVA_BENCH_DUMP=1` prints, per L3 case, each tool call, the route, the verifier
result, the result tables, and the start of the answer. Use it to see why a case
failed.

## Baseline

`baseline.json` holds the gated L0 and L2 results. The L3 results from the most
recent full run are in `l3-baseline.json`. Update a baseline only when a change
improves it, in the same commit as the change.
