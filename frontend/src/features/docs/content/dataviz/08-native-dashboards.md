---
title: Build a native dashboard
description: Create a dashboard, add a dataset, build charts and filters, arrange them, publish, and work on it with Neva.
---

## What it is

A **native dashboard** is a Nova dashboard made of charts, KPIs, tables and
text on a 12-column canvas. Every chart reads a **dataset** of the dashboard
and runs its query with your sign-in and active role, so row filters and
masks apply exactly as they do in a SQL worksheet. Each saved change becomes a
new revision; viewers only see what you publish.

## Before you start

- Open Nova Studio and click **Dataviz**.
- To add a dataset, your active role needs permission to read the table or
  view you choose. See [Who sees what](/docs/dataviz/who-sees-what).

## Create a dashboard

1. Click **New dashboard**.
2. Type a **Name**, choose a **Workspace**, and click **Create dashboard**.
   Personal dashboards stay private; dashboards in a shared workspace can be
   shared.
3. The editor opens on the **Canvas** tab.

## Add a dataset

1. Click the **Data** tab, then **Add dataset** and **New dataset**.
2. Type a **Dataset name**, choose a **Database** and a **Table or view**, and
   click **Inspect columns**. Nova lists the fields and their types without
   reading any rows.
3. Click **Save dataset**. Click **Run preview** to look at a few rows.

You can also build a dataset from a SQL query, with named parameters. See
[SQL dataset parameters](/docs/dataviz/native-documents#sql-dataset-parameters).

## Add charts

1. Go back to **Canvas** and click **Edit**. A dotted grid and the toolbar
   appear.
2. Click **Chart**, **KPI** or **Table**. The chart panel opens on the right
   with a live preview.
3. On **Configure**, type a **Chart name**, pick a visualization (KPI, Bar,
   Line, Area, Combo, Pie, Funnel, Table, Pivot, Scatter, Heatmap, Treemap,
   Flow, Histogram or Gauge), and choose the fields. For a date field, **Group by**
   lets you pick day, week, month, quarter or year. **Aggregate** chooses Sum,
   Average, Minimum, Maximum, Count or Distinct count.
4. On **Style**, choose the title, legend, data labels, colors and number
   format.
5. Click **Add to dashboard**. The chart is placed in the first free space.

### Pivot tables

A **Pivot** puts one or more fields down the side (**Rows**) and others across
the top (**Columns**), with a value in every cell. Choosing **Pivot** puts a
date across the top when the dataset has one; change it under **Columns**.
Under **Totals**, **Show totals** adds a total column and a total row, and
**Subtotal each row group** adds a total after each group of the first row
field. Totals add up sums and counts only; an average is never added up.

### Conditional formatting

For a **Table** or **Pivot**, open **Style** → **Conditional formatting** and
click **Add formatting**:

- **Colour scale** shades each value of the chosen measure from light to strong
  by where it falls between the smallest and the largest value shown.
- **Highlight when** colours a value that is **Above**, **At least**, **Below**,
  **At most** or **Equal to** the limit you type, in **Blue**, **Green**,
  **Red** or **Amber**. A matching highlight wins over a colour scale.

Click **Text** to add a heading or paragraph. Text is shown exactly as typed.

## Arrange the canvas

- In **Edit**, drag a widget to move it and drag its lower-right corner to
  resize it. Each completed move is saved as a new revision.
- Use **Undo** and **Redo** in the toolbar, or Ctrl+Z / Ctrl+Shift+Z
  (⌘Z / ⌘⇧Z on a Mac). Undo saves the previous layout as a new revision.
- Select a widget and press Alt with an arrow key to nudge it, or Alt+Shift
  with an arrow key to resize it.
- Open a widget's **…** menu for **Edit chart**, **Duplicate** or **Remove**.
- Click **+** next to the page name to add a page.
- Click **Done** to return to the view.

On narrow windows, widgets stack in one column and the layout cannot be
changed; the saved layout is kept for larger screens.

## Filters and selections

1. In **Edit**, click **Filters**.
2. Choose a **Dataset** and **Field**, pick a filter type (for example
   **Multiple values**, **Date range** or **Relative date**), change the
   **Label** if you like, and click **Add**.
3. Click **Apply filters**. The filters appear above the canvas.

Filters apply to every chart built on the same dataset. The values in a list
come from your role's own data. **Reset** clears all filters and selections.

Click a bar, point or slice to see what you can do with that value:

- **Filter the dashboard to this** filters the other charts on the same
  dataset by that value. The selection appears as **Selected:** with the value;
  click the **×** on it, or click the mark again, to clear it.
- **Drill into this by** shows the same chart for that value only, broken down
  by another field you choose, for example one region by channel, or one month
  by week. The chart title shows the path, such as **Revenue by region ›
  Jakarta**. Click the back arrow to go up one level, or **Reset** to return to
  the start. Drilling only changes your view; the saved chart stays the same.

A chart with nothing to drill into filters the dashboard straight away.

## Publish and share

1. Click **Publish**, optionally describe what changed, and click **Publish**.
   Viewers now see this version, including the chart and dataset versions it
   uses. The badge shows **Published** until you change the draft; **Publish**
   reappears once there is something new to publish.
2. Click **Share** to give a user or a role **Can view**, **Can edit**,
   **Can edit and publish** or **Can manage**, and optionally **Export**.
   Sharing also lets them open the charts and datasets the dashboard uses.
3. Click **Version history** (the clock icon) to see every saved version, who
   saved it and when. **Current** marks the version you are editing and
   **Published** the one viewers see.

> **Note:** Sharing never grants access to data. Each viewer sees only the rows
> and columns their own role can read.

## Download charts and data

Downloads need **Export** access to the dashboard. The owner has it; others get
it when the dashboard is shared with **Export** turned on. Every download is
recorded in the audit log, and a file holds only the rows your current role can
read, exactly as the charts show them with the filters you chose.

- **One chart:** point at the chart and click the download icon at its top right.
  Choose **Image (PNG)**, **Data (CSV)** or **Data (Excel)**. KPIs and tables
  have no image; download their data instead.
- **The whole page:** click **Download** (the arrow icon) in the dashboard
  header.
  - **Data of this page (Excel)** makes one workbook with a sheet per chart.
  - **Print or save as PDF** opens your browser's print window with a clean,
    light page: the title, when it was made, then every chart, KPI, table and
    note in reading order. Choose **Save as PDF** there to keep a file.

If the icons are missing, your access does not include **Export**; ask the
dashboard's owner. If access changed after you opened the page, the download is
refused and nothing is saved.

## Work with Neva

- Click **Ask Neva**, or the sparkle icon on a widget, to ask about the
  dashboard. You can also type in the **Assistant** panel, for example:

  ```text
  Add a line chart of total revenue per month to this dashboard.
  ```

- Neva reads the dashboard and its datasets and checks the numbers with your
  role. Before it changes anything, it shows the change and asks you to
  **Allow once** or **Deny**.
- When Neva saves a change, the canvas reloads the new revision on its own.

## Limits and errors

- "Query capacity is busy" means many charts are loading at once; Nova retries
  on its own. Click **Retry** if it stays.
- "The data source changed" means the table or query behind a dataset changed.
  Open the **Data** tab to review the dataset.
- "Someone else changed this dashboard" means another person or Neva saved
  first. The latest version is loaded; make your change again.
- Charts show up to the row limit you choose. A note under the chart says when
  more rows exist.
