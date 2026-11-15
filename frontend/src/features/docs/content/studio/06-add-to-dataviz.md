---
title: Add an answer to Dataviz
description: Turn a chart or table from an agent's answer into a live chart on a native Dataviz dashboard.
---

## What it is

**Add to Dataviz** takes a chart or table from an answer and places it, as a
live chart, on a native dashboard in [Dataviz](/docs/dataviz/what-is-dataviz).
Nova rebuilds the chart from the exact query that produced the answer, not from
a picture of it. Everyone who opens the dashboard later sees only the rows
their own role allows.

## Before you start

- The answer must be finished.
- To add to an existing dashboard, it must be one you can edit.

## Steps

1. In the answer, find the chart or table and click **Add to Dataviz** at its
   top right.
2. The **Add to Dataviz** window names the chart and, when Nova knows it, its
   **Business meaning**.
3. Keep **Existing dashboard** selected, type part of the name in
   **Search dashboards you can edit**, and click the dashboard in the list.
   To start a new dashboard instead, choose **New dashboard** and type its
   **Dashboard name**.
4. Click **Add chart** (or **Create and add** for a new dashboard).
5. A message confirms the chart was added. Click **Open in Dataviz**, or open
   the **Dataviz** tab yourself: it now shows the dashboard the chart went to.

Nova saves the query as a dataset of that dashboard, adds the chart in the
first free space, and keeps both in the dashboard's history. You can then move,
resize or edit the chart like any other. See
[Build a native dashboard](/docs/dataviz/native-dashboards).

## Limits and errors

- The button does not appear for results Nova cannot rebuild, for example an
  answer that was not produced by a query, or a map.
- If the dashboard was changed by someone else at the same moment, Nova tries
  again once; if that also fails, the message asks you to try again.
- Adding the same answer twice with one click (for example a double click) adds
  it once.
