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
   Flow, Histogram, Gauge or Map), and choose the fields. For a date field, **Group by**
   lets you pick day, week, month, quarter or year. **Aggregate** chooses Sum,
   Average, Minimum, Maximum, Count or Distinct count.
4. On **Style**, choose the title, legend, data labels, colors and number
   format.
5. Click **Add to dashboard**. The chart is placed in the first free space.

### Show a value as a calculation

For a bar, line, area, combo, pie, funnel or table chart, each value has a
**Show as** choice under its field: **Value** (the default), **Running total**,
**Percent of total**, **Difference from previous**, **Percent difference from
previous**, **Rank**, **Dense rank** or **Moving average** (choose how many
**Rows** it averages).

- The calculation runs over the rows the chart shows, after your role's data
  access and the filters, so it never adds data you cannot read.
- It goes along the first field you group by, in ascending order, and starts
  again for each series or other grouping. For example, a running total of
  revenue by month split by region adds up each region's months separately.
- Percent of total divides by the total of the rows shown in that series; it is
  labelled **(%)**.
- Equal values share a rank: **Rank** gives 1, 1, 3 and **Dense rank** gives
  1, 1, 2, with the highest value ranked first.
- A moving average uses the previous rows in order, not calendar days, so a
  month with no data is skipped rather than counted as zero.
- A missing value stays empty. Tables and pivots show no total for a
  calculated value, since adding up running totals or ranks means nothing.

A few rules apply to every chart:

- On a line, area or bar chart over dates, every period between the first and
  the last one is on the axis. A month with no data is a gap in the line,
  rather than disappearing, unless the chart is set to show missing values as
  zero. A chart sorted by its value keeps that order instead.
- When a chart is split by a field, each value keeps its own colour. Filtering
  out **North** does not repaint **West**.
- A KPI with nothing to show for the current filters says **No data for the
  current filters** under its dash.

Downloads, printed reports and scheduled files show the same calculated values.

### Tables

A **Table** shows 100 rows at a time. When there are more rows, a label such as
**Rows 101–200** and two arrows, **Previous page** and **Next page**, appear
under the table. Each page is read when you open it, so a long table loads
quickly, and a page you already opened shows again without waiting.

- Click a column heading to sort by it; click it again to reverse the order.
  Sorting starts again at the first page.
- Rows with the same value keep a fixed order, so moving between pages never
  shows a row twice or skips one.
- The table pages through rows up to the chart's **Row limit**. A note under
  the table says when more rows exist beyond it.
- With **Show a total row**, the total covers every row that matches the
  filters, not only the page on screen.
- A table with a **Show as** calculation reads all its rows at once, up to the
  row limit, because the calculation needs every row.

### Pivot tables

A **Pivot** puts one or more fields down the side (**Rows**) and others across
the top (**Columns**), with a value in every cell. Choosing **Pivot** puts a
date across the top when the dataset has one; change it under **Columns**.
Under **Totals**, **Show totals** adds a total column and a total row, and
**Subtotal each row group** adds a total after each group of the first row
field. Totals add up sums and counts only; an average is never added up.

### Maps

A **Map** places each row of the dataset at its latitude and longitude on a
street map. The dataset needs two number fields that hold them; fields named
latitude and longitude are picked for you, and you can change them under
**Location**.

- **Point size:** each point is a plain dot until you click **Size by a value**
  and choose a measure. Then the largest value gets the largest point, and the
  point's area grows with its value.
- **Color / group by:** colours the points by a field, such as country or
  region. The legend names up to eight groups; click a point to see its group
  and value.
- Rows without a valid latitude and longitude are not placed. The map says how
  many it left out.

Drag to move the map and scroll to zoom. Clicking a point with a group narrows
the other charts to that group, like clicking a bar. The street map is loaded
from an online map service, which sees the area you are looking at but none of
your data. If it cannot load, the chart says so and its data can still be
downloaded.

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

- In **Edit**, the canvas shows its grid as light squares: 12 across, filling
  the window and a few rows below the lowest widget. A widget always covers
  whole squares. The squares disappear when you click **Done**.
- In **Edit**, drag a widget to move it and drag its lower-right corner to
  resize it. Each completed move is saved as a new revision.
- Use **Undo** and **Redo** in the toolbar, or Ctrl+Z / Ctrl+Shift+Z
  (⌘Z / ⌘⇧Z on a Mac). Undo saves the previous layout as a new revision.
- Select a widget, by clicking it or moving to it with Tab, and press Alt with
  an arrow key to nudge it, or Alt+Shift with an arrow key to resize it.
- Empty rows you leave between widgets stay where you put them; widgets never
  slide up on their own.
- Open a widget's **…** menu for **Edit chart**, **Duplicate** or **Remove**.
- Click **+** next to the page name to add a page.
- Click **Done** to return to the view.

On a medium-width window, such as a tablet, the canvas shows six columns
scaled from the full layout. On a phone-width window, widgets stack in one
column. In both, the layout cannot be changed and the saved layout is kept for
larger screens.

## Filters and selections

1. In **Edit**, click **Filters**.
2. Choose a **Dataset** and **Field**, pick a filter type (for example
   **Multiple values**, **Date range** or **Relative date**), change the
   **Label** if you like, and click **Add**.
3. Click **Apply filters**. The filters appear above the canvas.

Filters apply to every chart built on the same dataset. The values in a list
come from your role's own data. **Reset** clears all filters and selections.

A **True / false** filter offers **All**, **True**, **False** and **No value**.
**True** and **False** never include rows where the field is empty; choose
**No value** to see only those rows.

Charts reuse a result for up to 30 seconds when you return to the same view,
so moving between pages is quick. Click **Refresh data** (the circular arrows
in the header) to run every chart again now, for example after a data load or
after an administrator changed what your role may read. Switching your role
always runs every chart again.

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

A KPI, or a chart that shows only totals, has no value to filter by, so
clicking it does nothing. Point at it to see why.

To select several values from one chart, hold **Shift** (or **Ctrl**, or
**Cmd** on a Mac) while you click. The other charts then show rows matching any
of the selected values, still narrowed by the dashboard filters and by
selections in other charts. Each value appears as its own **Selected:** chip.

When a chart has a legend, for example bars split by channel or a pie, clicking
a legend entry selects that group in the same way.

### Link a chart to another dashboard

1. In **Edit**, open a chart's **⋯** menu and choose **Link to a dashboard…**.
2. Choose the **Dashboard to open** and, under **Narrow it by**, the field there
   whose values match this chart's grouping (for example **Region**).
3. Optionally change the **Menu label**, then click **Save link**.

Now clicking a value offers **Open ‹dashboard›**. The other dashboard opens with
that value selected (for example **Selected Jakarta**); click the **×** on it to
see everything. People only get there if they can open that dashboard, and
they see only the rows their own role allows. To change or remove the link,
choose **Link to a dashboard…** again; **Remove link** deletes it. Removing the
chart also removes its link.

## Publish and share

1. Click **Publish**, optionally describe what changed, and click **Publish**.
   Viewers now see this version, including the chart and dataset versions it
   uses. The badge shows **Published** until you change the draft; **Publish**
   reappears once there is something new to publish.
2. Click **Share** to give a user or a role **Can view**, **Can edit**,
   **Can edit and publish** or **Can manage**, and optionally **Export**.
   Sharing also lets them open the charts and datasets the dashboard uses.
   **Can view** includes using filters, selections, drill-down and links. Access
   given as view only, without that, shows the published dashboard with its
   default filters: the filters appear as fixed labels and clicking a chart does
   nothing.
3. Click **Version history** (the clock icon) to see every saved version, who
   saved it and when. **Current** marks the version you are editing and
   **Published** the one viewers see.

> **Note:** Sharing never grants access to data. Each viewer sees only the rows
> and columns their own role can read.

## Comments

Click **Comments** (the speech-bubble icon) in the dashboard header to read
and write notes about the dashboard. The number on the icon counts open
comments.

- Under **About**, choose **The whole dashboard**, **This page**, or, when a
  widget is selected in **Edit**, **The selected widget**. Each comment shows
  who wrote it, when, and what it is about.
- **Resolve** marks a comment as done; **Show resolved** brings resolved
  comments back, and **Reopen** opens one again.
- The bin icon removes a comment. You can remove your own; people who manage
  the dashboard can remove any.

Anyone who can open the dashboard can read its comments. Writing and resolving
need **Comment** access, which **Can edit** and higher include. Comments are
shown exactly as written and never change the dashboard or its data.

## Accessibility

- Every drawn chart also carries a table of its rows for screen readers, so
  categories and values never depend on colour alone.
- When your system is set to reduce motion, charts appear without animation
  and other people's pointers jump instead of gliding.
- In **Edit**, press Tab to move from widget to widget; the focused widget is
  selected. Press **Alt** with an arrow key to move it, or **Alt** and
  **Shift** with an arrow key to resize it. A screen reader announces the new
  position, or that another widget is in the way.
- Closing the **Filters** window returns you to the button that opened it.

## See who else is here

When other people have the same dashboard open, their initials appear in the
header next to **Ask Neva**. A dot on an avatar means that person is editing;
point at an avatar to see their name. On the page you are looking at, each
person's pointer moves across the canvas with their name beside it.

Only people who can open the dashboard take part, and others see only your
user name and where your pointer is on the canvas, never your role or what you
have selected. Someone who closes the dashboard or switches to another browser
tab disappears within a few seconds. Two people can edit at the same time. If
both save at the same moment, the later save is not applied: the dashboard
reloads with the other person's change, the header says **Reloaded after
someone else’s change**, and you can make your change again.

## Download charts and data

Downloads need **Export** access to the dashboard. The owner has it; others get
it when the dashboard is shared with **Export** turned on. Every download is
recorded in the audit log, and a file holds only the rows your current role can
read, exactly as the charts show them with the filters you chose.

- **One chart:** point at the chart and click the download icon at its top right.
  Choose **Image (PNG)**, **Data (CSV)** or **Data (Excel)**. KPIs, tables and
  pivots have no image; download their data instead. A map's image is the map
  as it is drawn on your screen, at its current position and zoom.
- **The whole page:** click **Download** (the arrow icon) in the dashboard
  header.
  - **Data of this page (Excel)** makes one workbook with a sheet per chart.
  - **Print or save as PDF** opens your browser's print window with a clean,
    light report: the title, when the data was read, the filters and
    selections in effect, then every page of the dashboard, each starting on a
    new sheet, with its charts, KPIs, tables and notes in reading order. Choose
    **Save as PDF** there to keep a file.

If the icons are missing, your access does not include **Export**; ask the
dashboard's owner. If access changed after you opened the page, the download is
refused and nothing is saved.

### Schedule an Excel export

A schedule saves one page as an Excel workbook, one sheet per chart, on a
timetable. Click **Download** in the dashboard header, then **Schedule Excel
export…**. Choose the **Page**, how often it should **Repeat** (every day, week
or month), the day, the **Time** and the **Time zone**, then click **Save
schedule**. **Current schedules** in the same window lists every schedule for
this dashboard, with the role it runs as; click the bin icon to remove one of
yours.

Each file appears in the dashboard's workspace, in the folder **Scheduled
exports**, named after the dashboard, the page and the time it was scheduled
for. Charts use the dashboard filters' default values, as when you open the
page, and nothing is selected.

Each scheduled time saves one file. If a run is interrupted and tried again,
even minutes later, Nova finds the file the first attempt saved, checks that it
is complete and unchanged, and keeps it instead of saving a copy. If that file
is incomplete or was changed, the run fails rather than counting it as saved;
the next scheduled time saves its own file. If another file already has the
name, for example from another role's schedule at the same time, the new
file's name ends with a short code.

A schedule runs as your current role, through that role's scheduled-task
account, not as you. Each file holds what the role can read, with its row
filters and masks, even if you can see more. Because of that, Nova checks
before saving that:

- your current role is a business role, not an administration role;
- an administrator has set up a scheduled-task account for the role;
- the dashboard is published, and its published version has the page;
- the dashboard is in a shared workspace, shared with the role with edit
  access, so the files can be saved there;
- the dashboard is shared with the role, including **Export**.

If one of these is missing, the window says which one. Each run checks the
role's access again: if the dashboard is no longer shared with the role
including **Export**, the run saves nothing. Only people with the same role, or
an administrator, can remove a schedule.

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
