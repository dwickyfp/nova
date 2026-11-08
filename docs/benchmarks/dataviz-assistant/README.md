# Dataviz assistant: 5W+1H quality

The Dataviz assistant (`nova-dataviz-assistant`, [arch-19 D6](../../arch-19-studio-dataviz.md))
must answer what, who, when, where, why and how about the dashboard on screen,
from that dashboard's own data, with its verified filters and the Supernova
Business Ontology's definitions. This page records how that was measured.

## Method

- **Lab:** `backend/tests/benchmark/dataviz_assistant/run.py`, against the live
  local stack: the real model, Superset, StarRocks with Ranger, and the host
  backend.
  - `setup` builds the dashboard "Dataviz 5W1H eval" (4 charts and a Region
    native filter) on dataset `semantic_retail_sales_sales_orders`. That
    dataset is synced from the published Semantic View `retail_sales` v18.
  - `gold` computes the expected answers through Superset's chart data API.
  - `ask` creates a new assistant thread per question, sends the open
    dashboard (id, native filter key, edit state) exactly as the Studio panel
    does, and stores the answer, tool calls and finish reason.
- **Questions (Indonesian, 8 cases):**
  - what+how: definition and Oct 2026 total
  - who: top salesman
  - when: best month May–Oct
  - where: top region
  - why: Oct vs Sep change and its drivers
  - lineage: where the data comes from
  - filtered: total under the active Region filter
  - full 5W+1H: on units sold
- **Judging:** I acted as the LLM judge, strictly. Every number was checked
  against gold. A correct number inside a broken, dumped or wrong-language
  answer is a fail, as is a missing headline figure. Each verdict and its
  reason is in [judgements.json](judgements.json); every raw run is in
  [raw/](raw/).
- **Confidence intervals:** 95% Wilson score intervals.

## Gold answers

| Case | Gold |
| --- | --- |
| what/how | sales to outlets after discounts and returns, IDR, by order date; Oct 2026 Rp1.573.810.277,50 |
| who | Yusuf Santoso, Rp230.375.301 |
| when | October 2026, Rp1.573.810.277,50 |
| where | Jawa Barat & DKI, Rp807.421.961 |
| why | Rp1.573.810.277,50 vs Rp1.492.211.974,53, +Rp81.598.302,97 (5,47%); Jawa Barat & DKI +37.675.596, Jawa Timur +36.166.680,97 |
| lineage | dataset `semantic_retail_sales_sales_orders`, Semantic View `retail_sales` v18 |
| filtered | Region = Sulawesi applied, Rp148.289.709 |
| full 5W+1H | 329.840 vs 312.046 units, +17.794 (5,7%); Jawa Timur +9.232, Jawa Barat & DKI +6.966 |

## Result on the delivered build

Runs 22 and 23 ran on the final code:

- **16 of 16 correct, 95% CI 80.6–100%.**
- Median answer time 51 s, slowest 98 s.
- About 23.8k tokens per answer (median).

Every answer quotes exact numbers, states the dashboard filter it applied (or
that none is selected), and uses the ontology definition of the measure.
"Why" answers give both totals, the change and the drivers, and say that
drivers are contributors, not proof of cause.

## How it got there

Each row is a run on a different build, so the runs are not pooled.

| Run | Correct | What changed before it, and what still failed |
| --- | --- | --- |
| 9 | 4/8 | Dashboard data only. Failed: wrong column name, day instead of month grain, lineage guard, two dimensions in a comparison |
| 11–14 | 8, 7, 7, 7 | Business words resolve to columns (via column descriptions), month grain, lineage answered from verified metadata, one dimension per change. Failed: a comparison not asked for led to the biggest mover being named instead of the biggest earner; inline UI fallback |
| 15–18 | 8, 6, 7, 7 | Rounded values; text and tables only in the panel. Failed: the engine's number guard removed "net revenue naik Rp…" (no row named), and one provider timeout |
| 20–21 | 6, 6 | Comparison columns name their periods. Failed: a junk comparison period, "Units sold" not matching `units_sold`, the Total row ranked as a member, one answer in Portuguese |
| **22–23** | **8, 8** | Periods normalised, columns written with spaces, Total rows never ranked, one answer language |

Runs 10 and 19 are excluded: in 10 the backend reloaded mid-run, and in 19 the
eval client timed out before saving. The runner now saves after every case.

## Live user test (browser, Studio Dataviz tab)

Typed in the Assistant panel beside the open dashboard:

- **Why + who.** Total change and the top earner were correct. One error: a
  contributor's share was read from the neighbouring row. Fixed by giving each
  mover its own numbers (`top_contributors`).
- **Filtered.** A Region filter chosen in Superset reached the answer:
  Sulawesi, Rp148.289.709,00, "Region = Sulawesi applied".
- **Add a result (Scenario D).**
  - The assistant prepared the change and described it.
  - It asked first, and on "yes" in the next message showed the consent
    prompt with the server's own description.
  - It then added the table to the dashboard. Superset journal: `SUCCEEDED`,
    verified.
  - A role that cannot author gets `403 NOT_AUTHOR` and nothing changes.

## Limitations

- Eight questions on one dashboard and one dataset, in Indonesian. Not covered:
  multi-dataset dashboards, non-semantic datasets, viewer roles asking
  questions, and other languages.
- Answers take about 50 s at the median, dominated by the model provider. One
  provider timeout occurred in 14 judged runs.
- Result tables in Studio show raw numbers (`1573810277.5`). This predates the
  Dataviz work and applies to every Studio table.
