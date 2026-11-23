---
title: Ask the Dataviz assistant
description: Use the assistant beside a Dataviz dashboard to ask about its data and have it change the dashboard for you.
---

## What it is

The **Dataviz assistant** is the panel on the right of the Dataviz tab. It
answers questions about the dashboard you have open: what it shows, where its
data comes from, and what the numbers say. It can also build and change the
dashboard for you.

The assistant reads the dashboard's own datasets, so its numbers match the
charts. Like every chart, it queries with your sign-in and active role.

## Before you start

- Open Nova Studio and click **Dataviz**.
- Open a dashboard. Without one, the panel says "Open a dashboard to work on it
  together".

## Ask a question

1. Open a dashboard. The panel header shows **Dataviz assistant** and "Can read
   and edit this dashboard".
2. If the panel is closed, click **Assistant** at the top, or **Ask the
   assistant** in the dashboard header. On a phone the panel starts closed so
   the dashboard has the whole screen.
3. Type in **Ask a question about your data**, for example:

   ```text
   Which region has the most units on this dashboard?
   ```

4. Press **Enter**. The steps list shows the assistant reading the dashboard
   and querying its data.
5. Read the answer in the panel.

## Let the assistant change the dashboard

Ask for a change, for example:

```text
Add a line chart of total revenue per month to this dashboard.
```

The assistant can add or change charts (including pivot tables with totals, and
tables with shaded values), rearrange widgets, add filters, create a dashboard,
add a dataset and publish. It makes the change without asking first, as you and
with your active role, and each change is saved as a new version of the
dashboard. When a change is saved, the canvas loads the new version and marks
the widgets that changed with **Changed by the assistant**.

The steps list shows what each change did:

- **Rearranging widgets**: each widget by its title and what happens to it:
  moved (with its new column and row), resized (with its new width and
  height) or removed. A long list shows the first ten and how many more.
- **Adding filters**: each filter, the field it uses, and the charts it will
  filter. A filter that no chart uses yet says so.
- If a change cannot be saved as asked, for example because widgets would
  overlap, the step says why and nothing is saved.

If your request could mean more than one thing, for example which of several
dashboards or pages to change, the assistant asks you first. It never changes
anything until you have chosen.

The assistant says the dashboard changed, or was published, only after that
change was saved. If it did not save one in its reply, it says so, for example:
"No change was saved to the dashboard in this reply, so I won't say it
changed." Each saved change is listed under **Version history** (the clock
icon), with who saved it and when; see
[Build a native dashboard](/docs/dataviz/native-dashboards).

## Panel buttons

| Button | What it does |
| --- | --- |
| **New chat** | Starts a fresh conversation in the panel. |
| **Continue in Agent** | Opens the **Agent** tab, where you pick an agent for deeper analysis. |
| **Close assistant** | Hides the panel. **Assistant** at the top opens it again. |

## Limits

- The assistant knows the open dashboard, its pages, charts, datasets and
  filters, the page you are on and the widget you selected in **Edit**. It does
  not know which cross-filter you clicked.
- An answer usually takes under a minute.
- **Neva**, the assistant elsewhere in Nova, works differently: it asks before
  each action that changes something.
