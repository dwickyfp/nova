---
title: Ask Neva in Dataviz
description: Use Neva beside a Dataviz dashboard to ask about its data and, with your approval, change it.
---

## What it is

**Neva** is the panel on the right of the Dataviz tab. It answers questions
about the dashboard you have open: what it shows, where its data comes from,
and what the numbers say. It can also build and change the dashboard for you,
one approved step at a time.

Neva reads the dashboard's own datasets, so its numbers match the charts. Like
every chart, it queries with your sign-in and active role.

## Before you start

- Open Nova Studio and click **Dataviz**.
- Open a dashboard. Without one, the panel says "Open a dashboard to work on it
  together".

## Ask a question

1. Open a dashboard. The panel header shows **Neva** and "Can read and edit
   this dashboard".
2. If the panel is closed, click **Assistant** at the top, or **Ask Neva** in
   the dashboard header. On a phone the panel starts closed so the dashboard
   has the whole screen.
3. Type in **Ask a question about your data**, for example:

   ```text
   Which region has the most units on this dashboard?
   ```

4. Press **Enter**. The steps list shows Neva reading the dashboard and querying
   its data.
5. Read the answer in the panel.

## Let Neva change the dashboard

Ask for a change, for example:

```text
Add a line chart of total revenue per month to this dashboard.
```

Neva can add or change charts (including pivot tables with totals, and tables
with shaded values), rearrange widgets, add filters, create a dashboard, add a
dataset and publish. Before each change it shows what it will do with
**Allow once** and **Deny**. Nothing is saved until you allow it. When the
change is saved, the canvas loads the new version and marks the widgets that
changed with **Changed by Neva**.

What the approval shows:

- **Rearranging widgets**: each widget by its title and what happens to it:
  moved (with its new column and row), resized (with its new width and
  height) or removed. A long list shows the first ten and how many more.
- **Adding filters**: each filter, the field it uses, and the charts it will
  filter. A filter that no chart uses yet says so.
- If the change cannot be saved as asked, for example because widgets would
  overlap, the approval says why before you allow anything.

Neva says the dashboard changed, or was published, only after that change was
saved. If it did not save one in its reply, it says so, for example: "No change
was saved to the dashboard in this reply, so I won't say it changed."

## Panel buttons

| Button | What it does |
| --- | --- |
| **New chat** | Starts a fresh conversation in the panel. |
| **Continue in Agent** | Opens the **Agent** tab, where you pick an agent for deeper analysis. |
| **Close assistant** | Hides the panel. **Assistant** at the top opens it again. |

## Limits

- Neva knows the open dashboard, its pages, charts, datasets and filters, the
  page you are on and the widget you selected in **Edit**. It does not know
  which cross-filter you clicked.
- An answer usually takes under a minute; a change waits for your approval.
