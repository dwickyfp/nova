---
title: Edit and save a chart from an answer
description: Change a chart an agent drew in its answer, then save it to a Dataviz dashboard when you decide to keep it.
---

## What it is

When an agent draws a chart in an answer, the chart is a **draft**: it belongs
to the answer and is not saved anywhere else. You can open it beside the
conversation, change it, and save it to a
[Dataviz](/docs/dataviz/what-is-dataviz) dashboard. Nothing is saved until you
pick a dashboard and confirm. The agent can suggest saving a chart, but it
cannot save one for you.

A draft keeps only the chart's settings. Its rows are read again from the
answer's query, as you and under your current role, each time it is shown.

## Before you start

- The answer must be finished.
- To save, your current role must be able to read the data behind the chart.
- To save to an existing dashboard, it must be one you can edit.

## Open and edit a chart

1. In the answer, click the chart, or click **Open** at its top right. The
   chart opens in a panel beside the conversation.
2. Change what you need: the chart type, the fields on each axis, the
   measures, filters, sorting and title. The preview updates as you go.
3. Click **Apply changes**. The chart in the answer updates and shows
   **Edited**.

Your changes stay in this answer. They are still there when you reopen the
conversation from **History**.

## Save a chart to a dashboard

After an answer with a chart, Studio shows a suggestion:
"Save this chart to a Dataviz dashboard to keep it."

1. Click **Save to dashboard…** in the suggestion, or in the chart's panel.
2. In the **Save to dashboard** window, keep **Existing dashboard** selected
   and pick a dashboard from **Search dashboards you can edit**, or choose
   **New dashboard** and type its **Dashboard name**.
3. Click **Save chart** (or **Create and save** for a new dashboard).
4. A message confirms "Chart saved to the dashboard". The suggestion and the
   chart now show **Saved in** and the dashboard name. Click **Open** next to
   it to go to the dashboard.

Nova saves the chart as it looks at that moment, including your edits, with
its query as a dataset of that dashboard. Everyone who opens the dashboard
sees only the rows their own role allows.

## Limits and errors

- Studio suggests saving once per answer. After a chart is saved, the answer
  does not suggest it again.
- Saving the same chart twice with the same click does not add a second copy.
  To place another copy after more edits, click **Save to dashboard…** again.
- "Your active role … cannot read the data behind this chart, so it cannot be
  saved to a dashboard" means your current role may not read the chart's
  data. Switch to a role that can, and ask the question again.
- "This chart was made under another role or session" means you switched
  roles after the answer. Ask again under your current role to edit or save
  it.
- If someone else changed the same chart or dashboard at the same moment, the
  message asks you to try again.
- Tables, key figures and maps in an answer are kept with
  [Add to Dataviz](/docs/studio/add-to-dataviz) instead.
