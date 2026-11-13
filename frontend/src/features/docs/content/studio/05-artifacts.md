---
title: Artifacts
description: Find, open, edit, export and delete your saved charts and dashboards in Studio.
---

## What it is

An **artifact** is a chart or dashboard you keep in Studio. Each one is a live
view: when you open it, its query runs again with your current role, so the
numbers are up to date and show only the rows your role may see.

Artifacts are drawn by Nova's BI engine, the same one behind the
[Dataviz](/docs/dataviz/what-is-dataviz) tab and the **BI** page.

## Before you start

- To create charts and dashboards your active role must be allowed to author
  BI content. `ACCOUNTADMIN` always can; ask your administrator about other
  roles.
- To build a chart from a query, the role must be able to read the tables in
  that query.

## Open the list

1. In Studio's left rail, click **Artifacts**.
2. The page shows how many dashboards and charts you have and when one last
   changed.

Use **Search**, the **All**, **Dashboards**, **Charts** and **Favorites**
filters, and the sort menu (**Last changed**) at the top to narrow the list.

Dashboards appear as cards with a preview picture ("Preparing preview" while
it is being drawn). Charts appear in a table with their **Shape**, a one-line
**Current answer** (for example "Jakarta leads at 4.76K"), their **Source**
table, and when they were **Updated**.

## Create a chart from a query

1. Click **New** and choose **Chart from a query**.
2. In **Name**, type a name, for example `Units by region`.
3. In **Query**, type one read-only query. For example:

   ```sql
   SELECT region, SUM(units) AS units FROM DEMO_SALES.orders GROUP BY region ORDER BY units DESC
   ```

4. Click **Create**. The chart opens in the chart editor.
5. The chart starts as a table of the query's columns. To draw it another
   way, pick a chart type at the top of the **Data** panel (or
   **View all charts**), set the columns and metrics, and click
   **Update chart** to preview.
6. Click **Save**. In the **Save chart** window keep **Save (Overwrite)**, and
   click **Save**.
7. Click **Done editing** to return to Studio.

> **Tip:** In the **Save chart** window, **Add to dashboard** can also put the
> chart on a dashboard. Type a new name there and choose it to create a new
> dashboard ("A new dashboard will be created").

## Create a dashboard

1. Click **New** and choose **Dashboard**.
2. Type a **Name** and click **Create**. The empty dashboard opens in edit
   mode.
3. On the right, the **Charts** list shows charts you can use. Drag a chart
   onto the dashboard area on the left.
4. Click **Save**. The dashboard is published for your role (see
   [Who sees what](/docs/dataviz/who-sees-what)).
5. Click **Done editing**.

## Work with a chart

Open a chart from the list to see it full size. The header has:

| Button | What it does |
| --- | --- |
| **Edit chart** | Opens the chart editor. |
| **Refresh** | Runs the query again. |
| **Export** | Offers **CSV**, **Excel (.xlsx)**, **PDF** and **PowerPoint (.pptx)** downloads. |
| **Delete** | Deletes the chart after you confirm with **Delete artifact**. |

Below the chart, **Table** shows the rows and **SQL** shows the query.

## Work with a dashboard

Open a dashboard to view it. Use **Edit dashboard** to change its layout, and
**Delete** to remove it.

## Limits

- Deleting an artifact also deletes its chart or dashboard in BI. It cannot be
  undone.
- Dashboards made directly in BI or Dataviz, without Studio, are not listed
  under **Artifacts**. Find them on the [Dataviz](/docs/dataviz/what-is-dataviz)
  tab or the [BI](/docs/dataviz/bi-workspace) page.
- A chart's query must be a single read-only statement.

If a chart changes while you are editing it, reload the latest version before
applying your change. Nova refuses an update based on an older version.

For a shared legacy dashboard, only the charts in its saved layout are available.
Each chart runs with your current data access. Switching roles or losing the
share can make the dashboard unavailable.
