---
title: Add an answer to Dataviz
description: Turn a chart or table from an agent's answer into a live chart on a Dataviz dashboard.
---

## What it is

**Add to Dataviz** takes a chart or table from an answer and places it, as a
live chart, on a dashboard in [Dataviz](/docs/dataviz/what-is-dataviz). Nova
rebuilds the chart from the exact query that produced the answer, not from a
picture of it. Everyone who opens the dashboard later sees only the rows their
own role allows.

## Before you start

- The answer must be finished.
- Your active role must be allowed to author BI content (`ACCOUNTADMIN` always
  can). If it is not, the button does not appear.
- To add to an existing dashboard, it must be one you can edit.

## Steps

1. In the answer, find the chart or table and click **Add to Dataviz** at its
   top right.
2. The **Add to Dataviz** window names the chart and, when Nova knows it, its
   **Business meaning** (for example "Omzet", the business term for the
   metric).
3. Keep **Existing dashboard** selected, type part of the name in
   **Search dashboards you can edit**, and click the dashboard in the list.
   (To start a new dashboard instead, choose **New dashboard** and type its
   name.)
4. Click **Add chart**.
5. Wait for the window to close; on a busy system this can take up to a
   minute. Then open the dashboard on the **Dataviz** tab to see the chart.

The chart is added to the dashboard's layout. Charts already on the dashboard
stay where they are.

## Example

Ask a sales agent:

```text
Show net revenue per month from May to October 2026 as a line chart.
```

Click **Add to Dataviz** on the chart "Monthly net revenue (IDR), May–October
2026", choose **Existing dashboard**, pick a dashboard and click
**Add chart**. Open the dashboard in Dataviz: the line chart is there next to
its other charts and runs its own query every time the dashboard loads.

## When the button is missing

Nova shows **Add to Dataviz** only when it can rebuild the view exactly:

- **Maps** are not offered, because a dashboard chart cannot keep the map.
- Views that **recalculate** the rows (filters, groupings or "top N" applied
  inside the answer) are not offered.
- An answer still being written, or one from a role that may not author BI
  content, shows no button.

## Limits

- The dashboard chart has its own dataset built from the answer's query.
- Save or discard your own unsaved edits to a dashboard in Dataviz before you
  add a chart to it.
