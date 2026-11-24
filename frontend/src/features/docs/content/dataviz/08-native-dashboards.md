---
title: Build a native dashboard
description: Create a dashboard, add a dataset, build charts and filters, arrange them, publish, and work on it with the Dataviz assistant.
---

## What it is

A **native dashboard** is a Nova dashboard made of charts, KPIs, tables and
text placed on a page. Every chart reads a **dataset** of the dashboard
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

1. Go back to **Canvas** and click **Edit**. The page grid and the toolbar
   appear.
2. Click **Chart**, **KPI** or **Table**. The chart panel opens on the right
   with a live preview. To choose where it goes, drag the button onto the page
   instead: an outline shows the space it will take, and the chart is placed
   there when you add it. **Text** can be dragged the same way.
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
- A pie or funnel shows the values with the largest totals, up to the number
  in **Show the top** under **Slices** (12 unless you change it). On a pie of
  a Sum or Count, **Combine the rest into Other** (on for new pies) adds one
  **Other** slice worth exactly what was left out, so the percentages are
  shares of the real total. Otherwise the chart says **Showing the top 12
  only.** and shows no percentages, because it does not have the whole.
  Clicking **Other** filters nothing.

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

Drag to move the map. To zoom, hold Ctrl (⌘ on a Mac) while you scroll, or
pinch on a touch screen, where you move the map with two fingers; plain
scrolling scrolls the dashboard. **Show all locations**, in the top-right
corner of the map, brings every point back into view. Clicking a point with a group narrows
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
  A highlighted value also gets a small dot and bold text. Point at it to see
  the rule it passed, for example **Highlighted: above 1,000**; screen readers
  read the same words. The meaning doesn't depend on telling the colours apart.

Click **Text** to add a heading or paragraph. Text is shown exactly as typed.

## Arrange the canvas

Each page has a size, and the whole page is scaled to fit your window, so it
looks the same at any width, only larger or smaller. A dashboard made before
pages had sizes is 1,200 pixels wide and grows downwards; it scrolls as before.

- In **Edit**, the page shows its grid as light bands: 12 columns with a gap
  between them, and some room below the lowest widget to move things into. The
  grid is only a guide and disappears when you click **Done**.
- Click an item to select it. Hold Shift (or Ctrl, or ⌘ on a Mac) and click to
  add an item to the selection or take it out again. To select several at
  once, drag across empty page: everything the rectangle touches is selected.
  Ctrl+A (⌘A) selects everything on the page, and Escape clears the selection.
- Drag a selected item to move it; the other selected items move with it.
  With one item selected, drag one of the eight handles on its edges and
  corners to resize it. Press Escape during a drag to cancel it. Each completed
  move is saved as a new revision.
- While you drag, edges and centres snap to other items' edges and centres, to
  the middle and margins of the page, and to the column edges; anything else
  snaps to 8-pixel rows. Guide lines show what the item lined up with, and
  numbers show the gap in pixels to its nearest neighbours. Hold Alt (⌥ on a
  Mac) or ⌘ while dragging to place it freely.
- **Show grid** in the toolbar hides or shows the bands, and **Snap to grid**
  turns snapping off or on. Both are saved with the page, so everyone editing
  it sees the same. **Page and grid settings** sets the page size, how the page
  fits the window (**Whole page**, **Fit to width** or **Actual size**), the
  number of columns, the gutter between them, the margin, the row height and
  whether items snap to each other. The window tells you if the columns do not
  fit the page width.
- Items may overlap. An item added later is drawn on top.
- The **Arrange** menu acts on the selection:
  - **Align left**, **Align centre**, **Align right**, **Align top**,
    **Align middle** and **Align bottom** line several items up with each
    other. One item aligns with the page (or with its group).
  - **Space evenly across** and **Space evenly down** make the gaps between
    three or more items equal. The two outer items stay where they are.
  - **Auto-arrange page** puts each chart, filter, text and group on whole
    columns and rows, one after another in reading order, so nothing overlaps.
    Shapes, icons, lines and locked items stay where they are. With several
    items selected, it is **Auto-arrange selected** and moves only those. On a
    page with a fixed height, it tells you when the result would not fit.
  - **Bring to front**, **Bring forward**, **Send backward** and **Send to
    back** change which items are drawn on top.
  - **Group** puts two or more items into one group that moves as one. Its
    links between charts stay as they were. Double-click a group to work on
    the items inside it; click outside the group to close it. **Ungroup** puts
    the items back on the page where they were.
  - **Lock** stops an item from being moved or resized until you choose
    **Unlock**. **Hide** keeps an item on the page but hides it from viewers
    until you choose **Show**. In **Edit** it stays faintly visible.
  - **Duplicate** adds a copy a little below and to the right; **Delete**
    removes the selection.
- Use **Undo** and **Redo** in the toolbar, or Ctrl+Z / Ctrl+Shift+Z
  (⌘Z / ⌘⇧Z on a Mac). Every change above, from a move to new page settings,
  is one step. Undo saves the previous page as a new revision. Deleting an item
  from inside an open group cannot be undone; Nova says so when you do it, and
  Undo then starts again from that point.
- Select an item, by clicking it or moving to it with Tab, and press Alt with
  an arrow key to nudge it, or Alt+Shift with an arrow key to resize it. Left
  and right go to the next column; up and down move by 8 pixels. Several
  selected items move together.
- Empty rows you leave between items stay where you put them; items never
  slide up on their own.

- Open a widget's **…** menu for **Edit chart**, **Duplicate** or **Remove**.
- Click **+** next to the page name to add a page. A page holds up to 150
  items; when it is full, the add buttons are turned off and a note says
  so. Add another page for more.
- Click **Done** to return to the view.

Keyboard shortcuts in **Edit**:

| Shortcut (Windows and Linux) | On a Mac | Does |
| --- | --- | --- |
| Ctrl+A | ⌘A | Select everything on the page |
| Escape | Escape | Clear the selection |
| Delete or Backspace | Delete | Delete the selection |
| Ctrl+D | ⌘D | Duplicate |
| Ctrl+G / Ctrl+Shift+G | ⌘G / ⌘⇧G | Group / Ungroup |
| Ctrl+] / Ctrl+[ | ⌘] / ⌘[ | Bring forward / Send backward |
| Ctrl+Shift+] / Ctrl+Shift+[ | ⌘⇧] / ⌘⇧[ | Bring to front / Send to back |
| Ctrl+Shift+L | ⌘⇧L | Lock or unlock |
| Ctrl+Shift+H | ⌘⇧H | Hide or show |
| Alt+arrow / Alt+Shift+arrow | ⌥+arrow / ⌥⇧+arrow | Move / Resize |
| Ctrl+Z / Ctrl+Shift+Z | ⌘Z / ⌘⇧Z | Undo / Redo |

On a phone-width window, the widgets stack in one column, in reading order: top
to bottom, then left to right. The layout cannot be changed there, and the saved
page is kept for larger screens.

## Filters and selections

1. In **Edit**, click **Filters**.
2. Choose a **Dataset** and **Field**, pick a filter type (for example
   **Multiple values**, **Date range** or **Relative date**), change the
   **Label** if you like, and click **Add**.
3. Click **Apply filters**. The filters appear above the canvas.

Filters apply to every chart built on the same dataset. The values in a list
come from your role's own data. **Reset** clears all filters and selections.

A list shows up to 200 values. When there are more, it says so; type in
**Search values** to find the rest. A value you chose that is no longer in the
data stays chosen and is marked **Not in the data now**. Untick it to remove
it.

A list filter can follow another filter. When you add it, choose that filter
in **Values follow**; its list then shows only the values that match what is
chosen there, for example only the customers in the chosen city. The other
filter needs the same dataset or a field mapped to it. A value chosen earlier
that no longer matches is marked **Not in the current selection**. Removing
the filter it follows makes it an ordinary filter again.

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
   People also need access to the dashboard's workspace. If you share with a
   role the workspace isn't shared with, the dialog says so, and that role
   can't open the dashboard until the workspace owner adds it with
   **Share with roles…** on the workspace in **Workspaces**.
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
- A chart you can click to filter can also be used from the keyboard. Press
  Tab to reach it, then use the arrow keys to move from bar to bar or slice to
  slice. A screen reader announces each one. Press **Enter** to do what a click
  does, and **Escape** to leave the mark.
- When your system is set to reduce motion, charts appear without animation,
  loading placeholders stay still and other people's pointers jump instead of
  gliding. Widgets never slide on their own.
- Tab and screen readers go through the widgets in reading order: top to
  bottom, then left to right, whatever order they were added in.
- In **Edit**, press Tab to move from item to item; the focused item is
  selected. Press **Alt** with an arrow key to move it, or **Alt** and
  **Shift** with an arrow key to resize it. Every shortcut in
  [Arrange the canvas](#arrange-the-canvas) works from the keyboard, and the
  **Arrange** menu opens with Enter. A screen reader announces what each one
  did, such as the new position and size, "Grouped 2 items" or "3 items
  selected", or why it could not, such as an item being locked.
- Closing the **Filters** window returns you to the button that opened it.
- Every button, field and widget you reach with Tab shows a focus ring and has
  a name a screen reader can read. With a high-contrast theme (forced colours)
  turned on, the ring is drawn in your system's highlight colour.

## See who else is here

When other people have the same dashboard open, their initials appear in the
header next to **Ask the assistant**. A dot on an avatar means that person is editing;
point at an avatar to see their name. On the page you are looking at, each
person's pointer moves across the canvas with their name beside it.

Only people who can open the dashboard take part, and others see only your
user name and where your pointer is on the canvas, never your role or what you
have selected. Someone who closes the dashboard or switches to another browser
tab disappears within a few seconds. Two people can edit at the same time. If
both save at the same moment, the later save is not applied: the dashboard
reloads with the other person's change, the header says **Reloaded after
someone else’s change**, and you can make your change again.

While you are not saving, a change someone else saves appears on your canvas
within a few seconds. If your connection drops for a while, the dashboard
catches up with every change made meanwhile once it is back, and it never
goes back to an older version.

## Download charts and data

Downloads need **Export** access to the dashboard. The owner has it; others get
it when the dashboard is shared with **Export** turned on. Every download is
recorded in the audit log, and a file holds only the rows your current role can
read, exactly as the charts show them with the filters you chose.

- **One chart:** point at the chart and click the download icon at its top right.
  Choose **Image (PNG)**, **Data (CSV)** or **Data (Excel)**. KPIs, tables and
  pivots have no image; download their data instead. A map's image is the map
  as it is drawn on your screen, at its current position and zoom. If the
  chart is still updating after a filter change, the image waits until it
  shows the new result.
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

## Work with the Dataviz assistant

- Click **Ask the assistant**, or the sparkle icon on a widget, to ask about
  the dashboard. You can also type in the **Assistant** panel, for example:

  ```text
  Add a line chart of total revenue per month to this dashboard.
  ```

- The assistant reads the dashboard and its datasets and checks the numbers
  with your role. It makes the change you ask for without asking first, as
  you, and saves it as a new version. See
  [Ask the Dataviz assistant](/docs/dataviz/dataviz-assistant).
- The assistant sees the filters and chart selections you have applied. When it
  gives you numbers, it uses the same filters, or says which one it left out.
- When the assistant changes a chart, its step says what the chart shows
  afterwards, for example *It will show total Revenue by City instead of total
  Revenue and average Quantity by City*, and names anything the chart no longer
  shows. The assistant cannot change a calculation or a saved metric; change
  those yourself in the chart editor.
- While a change is being saved, an **Assistant** pointer sits on the widget it
  changes, labelled **Saving…**. It then shows **Saved**, or **Not saved** when
  the save failed; nothing changes on the dashboard unless it was saved. The
  pointer never takes your keyboard focus, and with reduced motion it appears
  in place instead of moving across the page.
- When the assistant saves a change, the canvas reloads the new revision on its
  own, and the widgets it changed are marked **Changed by the assistant** for a
  few seconds. A rearrangement of many widgets is saved as one version, listed
  under **Version history**.
- In a new chat, the assistant opens the dashboard beside the chat, or one you
  name, at its latest version, checked with your current role. If several
  dashboards match the name, it lists them and asks which one you mean. If the
  page you were on was removed meanwhile, it says so and asks which page to
  use.

## Limits and errors

- "Query capacity is still busy" means many charts are loading at once; Nova
  retries on its own. Click **Retry** if it stays.
- "The data engine is not responding" means the chart could not reach the data,
  or the engine was too busy to answer in time. It is not about your access.
  Click **Retry** in a moment.
- "The data source changed" means the table or query behind a dataset changed.
  The chart shows no data rather than reading the wrong column, including
  when a selection or filter uses a field that changed. If you can edit
  the dashboard, open the **Data** tab to review the dataset; otherwise ask
  someone who can.
- "Someone else changed this dashboard" means another person or the assistant saved
  first. The latest version is loaded; make your change again.
- If the connection drops while a change is saving, Nova checks whether the
  change was saved before deciding what to show. A saved change stays on the
  canvas and an unsaved one is taken back, so the same change is never saved
  twice. "The save could not be confirmed" means Nova could not check yet; the
  dashboard is reloaded to show what is saved. Look at the canvas before you
  make the change again.
- Charts show up to the row limit you choose. A note under the chart says when
  more rows exist.
- On a long page, a chart loads once you scroll near it, so a page with many
  charts opens quickly.
- "The chart is still updating" means a download was asked for while a chart
  was loading new results for more than 10 seconds. Download it again once it
  has finished.
