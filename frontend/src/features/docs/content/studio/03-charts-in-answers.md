---
title: Charts in answers
description: Read the charts and tables an agent draws in its answers, see the data behind them, and keep a chart.
---

## What it is

When an answer rests on data, the agent can draw it: a line chart for a trend,
a bar chart for a comparison, a table, a single number, or a map. These views
are drawn only from the rows the agent's query actually returned. The agent
cannot type numbers into a chart by hand.

## Ask for a chart

Name the chart you want in your question, or let the agent choose. For
example, with a sales agent:

```text
Show net revenue per month from May to October 2026 as a line chart.
```

The answer shows a short explanation, then the chart with its title (for
example "Monthly net revenue (IDR), May–October 2026"). Months run in time
order along the bottom and large values are shortened on the side axis (for
example **1.6B** for 1.6 billion). The table and chart appear as the answer
finishes; you do not need to reload the page.

## See the numbers behind a chart

- Some answers show the result table directly above the chart. Use **Filter
  rows** to find a value, or click a column heading to sort the table.
- Interactive answers may instead offer **View chart data** or **Source data
  (1 table)** to open the values behind the chart.
- Open the step summary at the top of the answer to see the SQL that produced
  the rows.

The note "Within these verified result rows" under a chart means that any
sorting or filtering in the view used only the rows already returned; it does
not fetch new data.

## Keep a chart

A chart in an answer stays in the conversation, so you can come back to it
from **History**. To keep it as a live chart on a dashboard, use
**Add to Dataviz** at the top right of the chart. See
[Add an answer to Dataviz](/docs/studio/add-to-dataviz).

## Limits

- **Add to Dataviz** appears only on charts and tables that Nova can rebuild
  exactly in Dataviz. Maps, and views that combine or recalculate rows, do not
  show it.
- A chart shows at most the rows the query returned. Ask a narrower question
  if you need more detail.
