# Phase 6b checkpoint: governed queries answer the usual reading

Date: 2026-11-10. Code: `feat/dataviz` after 12ec775. Same provider, runner and
lay-user rubric as [phase 6](phase-6-checkpoint.md), on all eight data tasks of
the golden set (DA01–DA08, both halves) × 3 runs = 24 turns.

Judging: one independent LLM judge applied the phase 6 rubric. It read the
phase 6 judgments for calibration and checked every stated figure against the
dev engine with read-only SQL. Evidence: `runs/phase-6b-readings.jsonl`,
`judgments/phase-6b-readings.jsonl`. 95% intervals: percentile bootstrap over
turns (4,000 resamples, seed 7).

## Why data questions failed

Five probes ran the Semantic View tool directly as the caller, and the stored
steps of live turns were read back. Together they showed four causes. None of
them was Neva's prompt.

| Cause | Example | Fix |
|---|---|---|
| The planner refused to pick among several fitting metrics | "penjualan" (net, gross, before returns), "returned the most" (units, value) | The planner answers the usual reading and records it with the alternatives (`readings`). The result tells the answer to say which reading it used and offer the others. A measure no metric fits is still asked back. |
| A request about how the result is shown blocked the whole query | "include the dates covered", "keep ties" | Such requests are never material. A request for the dates the data covers is marked `data_period`, and the tool reads them as the caller. |
| The number check refused years the data shows | "Oktober 2026" when the month column holds `date` values; the queried years in a year-over-year answer | Years are read from date-typed cells, from the queried period and from the data's own range. A year the data does not reach is still refused, and "2.026 units" is not read as a year. |
| The number check refused a number inside a name | "Mie Instan Goreng **85**g" | Result labels that contain digits are masked. A wrong figure next to the right name, or a name the result does not hold, is still refused. |

Live testing found one more bug. When an answer was rebuilt from result cells,
a money metric named `gross_margin` was written with a percent sign
("3.524.365.749,03%"), because column names with "margin" were assumed to be
percentages. Units declared in the Semantic View now travel with the result,
and a declared unit decides how a column is written.

## Results: data tasks

| Measure | Phase 6 (test half, 12 turns) | **Phase 6b (all 24)** | Phase 6b test half (12) |
|---|---|---|---|
| Solved | 0.38 | **0.88 (0.75–1.00)** | 0.75 (0.50–1.00) |
| Understandable | — | **1.00** | 1.00 |
| Correct | — | **0.88 (0.75–1.00)** | 0.75 |
| pass^3 | — | **0.88** (7 of 8 tasks) | — |
| Wrong and confident | 3 (all DA04) | **3 (all DA04)** | 3 |
| Latency p50 / p95 | — | **37.0 / 59.2 s** | — |

Solved per task (out of 3): DA01 3, DA02 3, DA03 3, DA04 0, DA05 3, DA06 3,
DA07 3, DA08 3. In phase 6, DA05 and DA08 failed every run, and DA02, DA03 and
DA07 asked back on the dev tasks.

DA02 was run again after the label fix. The other 21 turns ran on the same
planner code before it. Every stated figure matches the engine: for example,
Mie Instan Goreng 85g with 2,745 units returned, October 2026 with
Rp1,573,810,277.50, and a gross margin of Rp3,524,365,749.03.

## Phase 6c: the official view, the answering view, rounded figures

The 6b run left three problems. This section records how each was fixed.

| Problem | Fix |
|---|---|
| DA04 answered 5,000 orders from the benchmark view. "Orders" names no glossary term, so the model picked the view itself. | The planned metric's whole name (`order_count`) is the approved concept Order count, and that concept is computed in `retail_sales`. The tool returns a recoverable `OFFICIAL_VIEW_REQUIRED` naming that view. The model asks there, so its own call names the source it cites. Only a whole metric name counts. |
| The answer named the view the model asked for, not the view that answered. | Every result now states the view that answered (`source:`). |
| A rounded figure noted as derived ("Rp3,52 miliar" of `gross_margin`) was refused. | A derived claim with no change or comparison is accepted when it rounds a cell of the column it names. Another column, a claimed change or no column is still refused. |
| "No data for 2025" was refused when the year came from a query that returned no rows. | The days a query reported without data, and the range the data covers, back the years they name. They are checked by the number check only, and no figure is ever taken from them. |

Results on all eight data tasks × 3 runs, judged as above. Every stated figure
was checked on the engine. Evidence: `runs/phase-6c-views.jsonl` and
`judgments/phase-6c-views.jsonl`.

| Measure | Phase 6 (test half) | Phase 6b | **Phase 6c (all 24)** | Phase 6c test half |
|---|---|---|---|---|
| Solved | 0.38 | 0.88 | **0.96 (0.88–1.00)** | 0.92 (0.75–1.00) |
| Correct | — | 0.88 | **1.00** | 1.00 |
| Understandable | — | 1.00 | **0.96** | 1.00 |
| Wrong and confident | 3 | 3 | **0** | 0 |
| pass^3 | — | 0.88 | **0.88** | — |
| Latency p50 / p95 | — | 37.0 / 59.2 s | **27.1 / 66.7 s** | — |

Solved per task: DA01 to DA07 3 of 3, DA08 2 of 3. The DA08 failure is a model
call the provider held for 145 s. The turn ended plainly within its time
budget, without guessing.

## What still fails

1. **Terse fallback (DA01 run 1, DA07 run 1).** Neva's draft stated a figure
   the check could not back: a year for a whole-history answer that did not
   ask for its dates, or a rounded figure with a claim shape the run did not
   record. The answer was then rebuilt from cells. The figures are correct,
   but the reply is bare.
2. **"Could not confirm the date range"** (DA05, DA08). The tool reads the
   data's dates only when the model asks for them.
3. **Jargon.** "Semantic View" and view names such as `retail_sales` appear in
   almost every reply (phase 6 item 4).
