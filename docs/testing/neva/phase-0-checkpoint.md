# Phase 0 checkpoint: Nove baseline

Date: 2026-11-09. Code: `feat/dataviz` at e855029 (before any Neva backend
change). Provider: the AI provider configured on the local stack (the panel's
default model). Runner: `backend/scripts/neva_golden_run.py`, 40 golden tasks
× 3 runs = 120 turns, asked as a cautious lay maintainer (read-only checks
allowed once, every change declined). Judge: an LLM judge with the rubric in
`golden-set.json`, facts checked against the live engine with read-only SQL.

Evidence:
- Transcripts: `runs/phase-0-nove-baseline.jsonl`
- Verdicts: `judgments/phase-0-nove-baseline.jsonl`

95% intervals are percentile bootstrap over turns (4,000 resamples, seed 7).

## Results

| Measure | All 120 turns | Test half (60 turns) |
|---|---|---|
| Solved | 0.21 (0.15–0.28) | 0.21 (0.13–0.29) |
| Understandable to a lay maintainer | 0.41 (0.33–0.50) | 0.42 (0.30–0.53) |
| Correct | 0.50 (0.41–0.59) | 0.55 (0.43–0.68) |
| Asked before changing | 0.98 (0.96–1.00) | 0.98 (0.95–1.00) |
| pass^3 (solved in all 3 runs) | 0.075 of tasks | 0.05 of tasks |
| Wrong and confident | 3 turns | 0 turns |
| Answers with no text at all | 44 turns (37%) | |
| Automatic technical-leak hits | 3 turns | |
| Judge: technical words a lay user would not follow | 36 turns | |
| Latency p50 / p95 | 20.2 s / 47.4 s | |
| Tokens per turn, median / p95 | 5,024 / 13,216 | |

By category (solved, understandable; all turns):

| Category | Solved | Understandable |
|---|---|---|
| explain | 0.46 (0.31–0.63) | 0.88 (0.75–1.00) |
| find | 0.25 (0.08–0.44) | 0.61 (0.39–0.83) |
| fix SQL | 0.08 (0.00–0.22) | 0.22 (0.06–0.44) |
| data | 0.06 (0.00–0.13) | 0.13 (0.00–0.25) |
| access | 0.11 (0.03–0.22) | 0.11 (0.00–0.28) |
| maintain | 0.28 (0.14–0.44) | 0.44 (0.22–0.67) |

## What fails

1. **No answer at all (37%).** The planner names a required capability; the
   model loads a guide, then stops without using it, and the turn ends with
   "The required capability was not executed…" (35 turns) or "could not plan
   this request safely" (3). Data questions are hit hardest: 20 of 24 data
   turns are empty and none returned a number, although the sales table is in
   the catalog it searched.
2. **False "does not exist".** "No semantic views" (six exist), "no price
   table" (`products.list_price`), "no finance role" (it exists).
3. **Asks the person for technical names** (schema, table, column) instead of
   looking them up.
4. **Invented SQL** (an `orders` table with `created_at` instead of
   `sales_orders.order_date`).
5. **Technical words**: schema, Ranger, metadata, `/ai-providers`, endpoint vs
   `endpoint_url`, raw session states. Wrong menu path for running queries.

## Targets for Neva (test half, same set, same provider)

- Solved and understandable clearly above the baseline intervals, every
  category at least as good as here.
- Zero automatic technical leaks; judge technical-word turns well below 36/120.
- No empty answers caused by a missing required capability.
- Asked before changing stays at or above 0.98.
- Latency for simple explain/find tasks not worse than the baseline p50 by more
  than the analyst budget's extra steps justify; reported per category.
