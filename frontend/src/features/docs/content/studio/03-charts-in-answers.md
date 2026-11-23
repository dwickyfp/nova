---
title: Charts in answers
description: Read the charts, overview pages, maps and what-if controls an agent draws in its answers, see the data behind them, and keep a chart.
---

## What it is

When an answer rests on data, the agent can draw it: a line chart for a trend,
a bar chart for a comparison, key figures, a table, or a map. Charts in answers
are the same charts as in [Dataviz](/docs/dataviz/what-is-dataviz). They read
their rows from the query behind the answer, as you and under your current
role, so the same row filters and masked columns apply. Key figures are read
from the returned rows. The agent cannot type numbers into a chart or a figure
by hand.

## How a data answer reads

A data answer is laid out like a short report:

1. One sentence with the main finding.
2. The key figures, when the question asks for totals or a comparison.
3. The charts.
4. A short explanation of what the charts show.
5. **Source data**, folded, with the tables the answer used.

## Overview pages

Ask for an overview, a dashboard, a comparison of several figures or a
what-if, and the answer is laid out as a page instead of a paragraph. For
example:

```text
Give me a sales overview for June and July 2025, with a what-if slider for revenue.
```

The page builds up while the agent writes it: the title and a one-line
summary appear first, then each section as soon as it is ready. A page can
hold:

- a title and a short summary of what it shows;
- key figures, each with its change (for example **+12.4%** in green, or a
  red figure for a drop) and a short note such as "vs. June";
- charts, the same live charts as everywhere else in Studio;
- tables of the returned rows;
- a short explanation;
- what-if controls: sliders, choices and switches.

A single figure or a short list is still answered in a sentence, not a page.

## What-if controls

Moving a slider or picking an option recalculates the scenario figures on the
page straight away. The controls only recalculate figures already on the
page. They do not run a new query, and nothing you set is sent to the agent or
saved: when you reopen the conversation, the controls start again from the
values the agent chose. Scenario figures are assumptions, and the page labels
them as scenarios. The figures read from your data do not move.

## Maps and pictures

Ask for a map of results that have coordinates, or that name cities, and the
page shows them on a map. Results that only name a city are placed at the
centre of that city. Click a place, or **Select** it under **View locations**,
to see its name and figure. For example:

```text
Show revenue by city on a map.
```

You can also ask for public places and the road between them:

```text
Show the road route from Jakarta to Bandung.
```

The map shows the places, the fastest road route, its distance and driving
time, and up to two alternative routes. Click an alternative beside the map to
draw it. Ask for several places to see one route through them in order, or ask
for the shortest order to let the agent choose it, starting from the first
place. Questions in Indonesian work the same way ("rute tercepat dari Semarang
ke Malang lewat jalan").

- Only the city names and their rough positions are used to find the route.
  The customers, figures and other data on your page are not sent anywhere.
- When there is no road between the places, for example between two islands,
  the map shows the places and says that no road joins them.
- A place the agent could not match to a known city has a dashed ring:
  its position is approximate.
- Routes and pictures are saved with the answer, so a reopened conversation
  shows the same map without looking them up again.

Ask for a picture of a public place or thing, such as "Show a picture of
Borobudur", and the page shows a picture from Wikimedia Commons with a link
to its page there. When no picture can be found or loaded, the page shows
"Image unavailable" with a link to search Wikimedia Commons, and the rest of
the answer still appears.

## Ask for a chart

Name the chart you want in your question, or let the agent choose. For
example, with a sales agent:

```text
Show net revenue per month from May to October 2026 as a line chart.
```

The answer starts with its main finding, then shows the chart with its title
(for example "Monthly net revenue (IDR), May–October 2026"). Months run in time
order along the bottom and large values are shortened on the side axis (for
example **1.6B** for 1.6 billion). Hover over a point or bar to see its exact
value. The chart fills in as the answer finishes; you do not need to reload
the page.

## See the numbers behind a chart

- Open **Source data (1 table)** under the answer to see the rows behind its
  charts. Use **Filter rows** to find a value, or click a column heading to
  sort the table.
- Answers without a chart show the result table directly.
- Open the step summary at the top of the answer to see the SQL that produced
  the rows.

## Keep a chart

A chart in an answer stays in the conversation, so you can come back to it
from **History**. Click a chart to open it beside the conversation, change it,
and save it to a dashboard when you decide to keep it. Nothing is saved until
you confirm. See [Edit and save a chart from an answer](/docs/studio/edit-and-save-charts).
Tables use **Add to Dataviz**; see
[Add an answer to Dataviz](/docs/studio/add-to-dataviz).

## Limits

- A chart in an answer shows data only while you use the role you asked the
  question with. After you switch roles, the chart says "This chart was made
  under another role or session. Ask again under your current role to see
  it." Ask the question again under the new role.
- Only you can see the charts in your own conversations.
- Interactive answers from earlier versions of Studio show their figures,
  charts and tables as a page, marked "An earlier interactive answer, shown
  read-only." Their old buttons, forms and saved inputs are no longer
  available.
- Every figure on a page comes from the returned rows. If the agent writes a
  figure the rows do not support, that part of the page is left out and the
  rest still shows.

- **Add to Dataviz** appears only on charts and tables that Nova can rebuild
  exactly in Dataviz. Maps, and views that combine or recalculate rows, do not
  show it.
- A chart shows at most the rows the query returned. Ask a narrower question
  if you need more detail.
- "This interactive answer is too large to show. Here is the written answer."
  means the page was bigger than Studio can display, so the written answer is
  shown in its place. Ask for fewer charts or a narrower result to get a
  page.
