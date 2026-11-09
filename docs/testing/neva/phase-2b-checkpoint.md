# Phase 2b checkpoint: Neva after the measured fixes

Date: 2026-11-09. Code: `feat/dataviz` at e401db2 (Neva code; the last 31 turns
ran after 1062630, which only removed the variables module's password policy
and does not touch Neva). Same provider, runner, golden set, lay-user rubric
and judge as [phase 0](phase-0-checkpoint.md) and [phase 2](phase-2-checkpoint.md):
40 tasks × 3 runs = 120 turns. Facts checked against the engine with read-only
SQL (best month for sales, revenue per channel, order counts per dataset).

Runner changes since phase 2 (closer to the real app, see `backend/scripts/neva_golden_run.py`):
SQL tasks also send the `query_failed` event the SQL Workspace sends, and each
stream is read to the end like the panel.

Evidence: `runs/phase-2b-neva.jsonl`, `judgments/phase-2b-neva.jsonl`.
95% intervals: percentile bootstrap over turns (4,000 resamples, seed 7).

## Results

| Measure | Phase 0 (Nove) | Phase 2 | **Phase 2b** | Phase 2b test half |
|---|---|---|---|---|
| Solved | 0.21 (0.15–0.28) | 0.42 (0.34–0.51) | **0.67 (0.59–0.74)** | 0.77 (0.67–0.87) |
| Understandable | 0.41 (0.33–0.50) | 0.93 (0.88–0.97) | **0.92 (0.87–0.97)** | 0.97 (0.92–1.00) |
| Correct | 0.50 (0.41–0.59) | 0.70 (0.62–0.78) | **0.95 (0.91–0.98)** | 0.93 (0.87–0.98) |
| Asked before changing | 0.98 | 1.00 | **1.00** | 1.00 |
| pass^3 | 0.075 | 0.20 | **0.50** | 0.65 |
| Wrong and confident | 3 | 19 | **4** | 4 |
| Answers with no text | 44 | 7 | **0** | |
| Automatic technical-leak hits | 3 | 0 | **0** | |
| Judge: technical words | 36 | 24 | 28 | |
| Latency p50 / p95 | 20.2 / 47.4 s | 28.8 / 65.5 s | 33.5 / 86.8 s | |

Solved by category (phase 0 → 2b): explain 0.46 → 0.94, find 0.25 → 1.00,
fix SQL 0.08 → 0.61, data 0.06 → 0.46, access 0.11 → 0.56, maintain 0.28 → 0.42.
No category is worse than phase 0.

## What still fails

1. **Wrong and confident (4 turns).** "How many orders" answered 5,000 from the
   benchmark business view without naming it (the retail data has 6,758), and a
   year-over-year question answered with one all-time figure. Needs a source
   label on every governed answer and a check that the answer matches the
   question (phase 5.3, 5.5).
2. **Raw fallback text (4 turns).** When the model's numbers fail verification,
   the reply becomes "Here are the figures." with raw rows (internal task ids,
   8-decimal numbers). Unreadable; it must be rewritten in plain words.
3. **"I won't guess" (12 turns).** The access check and grant flow (AC02, AC03)
   and stopping a heavy query (MO05) still end there.
4. **Not looked up:** slow report (MO01) and health (MO03) do not read query
   history or health; the open view's formula (EX03) is not read.
5. **Says it cannot create a user** although it can (AC04, 2 of 3).
6. **Slower:** p50 33.5 s. Phase 2.3 (automatic fast/analyst budget) addresses it.
