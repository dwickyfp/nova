---
title: Ask the Dataviz assistant
description: Use the assistant beside a Dataviz dashboard to ask about the charts and data on screen.
---

## What it is

The **Dataviz assistant** is the panel on the right of the Dataviz tab. It
answers questions about the dashboard you have open: what it shows, where its
data comes from, and what the numbers say. You do not choose an agent here;
Nova uses its own dashboard assistant, which reads the dashboard on screen.

It answers from the dashboard's own datasets, so its numbers match the charts.
Like every chart, it queries with your sign-in and active role.

## Before you start

- Open Nova Studio and click **Dataviz**.
- Open a dashboard. Without one, the panel says "Open a dashboard to ask about
  it".

## Steps

1. Open a dashboard in Dataviz. The panel header changes to
   **Reading** followed by the dashboard name, for example "Reading Sales
   overview".
2. If the panel is closed, click **Assistant** at the top.
3. Click a suggested question, or type your own in **Ask a question about your
   data**, for example:

   ```text
   Which region has the most units on this dashboard?
   ```

4. Press **Enter**. The **Working** line lists each step: it first reads the
   dashboard (its charts, datasets and filters), then queries the dashboard's
   data.
5. Read the answer in the panel.

## Panel buttons

| Button | What it does |
| --- | --- |
| **New chat** | Starts a fresh conversation in the panel. |
| **Continue in Agent** | Moves the question to the **Agent** tab, where you pick an agent for deeper analysis. |
| **Close assistant** | Hides the panel. **Assistant** at the top opens it again. |

## Limits and errors

- The assistant knows the open dashboard, its charts and datasets, and the
  dashboard's native filters. It does not know which chart you clicked or
  which cross-filter you selected.
- An answer takes about a minute.
- "The dashboard could not be read right now" followed by a reason means the
  BI tool did not answer in time. Wait a moment and ask again.
