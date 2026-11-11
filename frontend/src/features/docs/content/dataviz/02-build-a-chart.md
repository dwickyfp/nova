---
title: Build a chart
description: Create a chart from a SQL query, choose how it is drawn, and save it.
---

## What it is

A chart in Dataviz reads its rows from a **dataset**: a saved query. The
simplest way to start is to give Nova a query; Nova makes the dataset and an
empty chart for you, and opens the chart editor.

## Before you start

- Your active role must be allowed to author BI content. `ACCOUNTADMIN`
  always can; ask your administrator about other roles.
- Your role must be able to read the tables in your query. Try the query in a
  SQL worksheet first.

## Steps

1. In Nova Studio, click **Artifacts** in the left rail.
2. Click **New** and choose **Chart from a query**.
3. In **Name**, type a name, for example `Units by region`.
4. In **Query**, type a single read-only query, for example:

   ```sql
   SELECT region, SUM(units) AS units FROM DEMO_SALES.orders GROUP BY region ORDER BY units DESC
   ```

   This query returned five rows (Jakarta 4764, Surabaya 3504, Bandung 2688,
   Medan 2539, Makassar 1762) on the demo data.

5. Click **Create**. After a few seconds the chart editor opens with a
   **Table** of your query's columns.
6. To draw it differently, pick another chart type at the top of the **Data**
   panel, or click **View all charts**. Set the fields the chart asks for
   (for example the column for the axis and the metric to plot) and click
   **Update chart**.
7. Click **Save**. In the **Save chart** window:
   - keep **Save (Overwrite)** and the **Chart name**;
   - optionally choose a dashboard in **Add to dashboard** (see
     [Build a dashboard](/docs/dataviz/build-a-dashboard)).
8. Click **Save**. Nova shows "Chart [name] has been overwritten".
9. Click **Done editing**.

The chart is listed under **Artifacts** with a one-line summary such as
"Jakarta leads at 4.76K", and under **Charts** in Dataviz.

## Change a chart later

1. Open it from **Artifacts** and click **Edit chart**.
2. Make your changes in the editor and click **Save**.
3. Click **Done editing**.

## Limits and errors

- A chart reads one query. To combine data, join it in the query.
- In the **Raw records** query mode the table does not keep your query's
  `ORDER BY`: in the example above the rows came back in a different order.
  Use the **Ordering** field in the editor if the order matters.
