# Phase 2 checkpoint: Neva after the harness work

Date: 2026-11-09. Code: `feat/dataviz` at 86b9a08 (phase 1 and 2 changes,
role rules). Same provider, runner, golden set and lay-user rubric as
[phase 0](phase-0-checkpoint.md): 40 tasks × 3 runs = 120 turns, read-only
checks allowed once, every change declined. Judge: the same LLM judge and
rubric; facts checked against the live engine with read-only SQL (revenue per
city, order count, price columns).

Evidence:
- Transcripts: `runs/phase-2-neva.jsonl`
- Verdicts: `judgments/phase-2-neva.jsonl`

95% intervals: percentile bootstrap over turns (4,000 resamples, seed 7).

## Results

| Measure | Phase 0 (Nove), all | Phase 2 (Neva), all | Phase 2, test half |
|---|---|---|---|
| Solved | 0.21 (0.15–0.28) | **0.42 (0.34–0.51)** | 0.42 (0.30–0.54) |
| Understandable to a lay maintainer | 0.41 (0.33–0.50) | **0.93 (0.88–0.97)** | 0.92 (0.83–0.98) |
| Correct | 0.50 (0.41–0.59) | **0.70 (0.62–0.78)** | 0.72 (0.60–0.83) |
| Asked before changing | 0.98 (0.96–1.00) | 1.00 | 1.00 |
| pass^3 (solved in all 3 runs) | 0.075 of tasks | 0.20 | 0.20 |
| Wrong and confident | 3 turns | **19 turns (worse)** | 7 turns |
| Answers with no text | 44 (37%) | 7 (6%) | |
| Automatic technical-leak hits | 3 | 0 | |
| Judge: technical words | 36 turns | 24 turns | |
| Latency p50 / p95 | 20.2 s / 47.4 s | 28.8 s / 65.5 s (slower) | |
| Tokens per turn, median | 5,024 | 12,359 | |

The phase 0 test-half intervals (solved 0.13–0.29, understandable 0.30–0.53)
do not overlap Neva's, so both gains are measured, not noise. "Understandable"
counts the fixed "I won't guess" message as understandable; it is never
counted as solved.

By category (solved, understandable; all turns):

| Category | Phase 0 solved | Neva solved | Neva understandable |
|---|---|---|---|
| explain | 0.46 | 0.71 (0.54–0.85) | 1.00 |
| find | 0.25 | 0.53 (0.31–0.75) | 0.83 (0.67–1.00) |
| fix SQL | 0.08 | 0.11 (0.00–0.25) | 0.94 (0.83–1.00) |
| data | 0.06 | 0.25 (0.10–0.42) | 0.96 (0.88–1.00) |
| access | 0.11 | 0.50 (0.28–0.72) | 0.83 (0.67–1.00) |
| maintain | 0.28 | 0.42 (0.19–0.64) | 0.94 (0.83–1.00) |

No category is worse on solved or understandable. "Wrong and confident" is
worse and blocks acceptance.

## What still fails

1. **Wrong governed view (16 of 19 wrong-and-confident turns).** Data questions
   about sales, returns, stock or orders are answered from the deliveries view,
   and Neva says "there is no sales data". Only revenue per city (DA01) reached
   the retail view. The views list is ordered by relevance, but the governed
   tool answers from the wrong one.
2. **A failed read ends the turn with nothing (6 turns).** A catalog lookup on
   a database name (FI01), an unknown system table (FI04), a user name with the
   wrong case (AC01) and a database-wide access check (AC02) end the turn as an
   error, and the person sees a raw engine message or nothing.
3. **"I won't guess" too often (19 turns).** Mostly fix-SQL questions: the
   planner requires a check the model never runs (SQ01–SQ05, AC03, AC05, MO05).
   Honest, but not helpful.
4. **No way to list Semantic Views** (FI03), and the open view's own
   definition is not read (EX03).
5. **Knowledge gaps:** Query Autopilot unknown (EX04); wrong menu for running
   queries (FI02, "Active Queries" instead of Monitoring > Queries > Running
   now); no health check (MO03); slow-report questions do not look at query
   history (MO01).
6. **Slower:** p50 +8.6 s from the analyst budget and the governed tool.

## Decision

Keep the phase 1–2 changes (solved ×2, understandable ×2.3, no empty-answer
class left at 37%). Fix 1 and 2 before any other phase; they are correctness
and safety of answers, not polish. Re-run the same set after the fixes.
