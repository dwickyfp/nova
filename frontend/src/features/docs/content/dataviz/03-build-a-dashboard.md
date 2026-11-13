---
title: Build a dashboard
description: Create a dashboard, place charts on it, save it, and open it in Dataviz.
---

## What it is

A **dashboard** is a page that arranges several charts. When anyone opens it,
each chart runs its own query with that person's sign-in and role.

## Before you start

- Your active role must be allowed to author BI content (`ACCOUNTADMIN` always
  can).
- Have at least one chart. See [Build a chart](/docs/dataviz/build-a-chart).

Choose one of the three ways below to put charts on a dashboard.

## Option 1: create an empty dashboard and drag charts onto it

1. In Nova Studio, click **Artifacts**.
2. Click **New** and choose **Dashboard**.
3. Type a **Name** and click **Create**. The dashboard opens in edit mode with
   "Drag and drop components and charts to the dashboard".
4. On the right, the **Charts** tab lists charts you can use (**Show only my
   charts** is ticked by default). Use **Filter your charts** to find one.
5. Drag a chart card from the list onto the empty area on the left. The card
   in the list is then marked **Added**.
6. Repeat for more charts.
7. Click **Save**. The dashboard leaves edit mode and shows **Published**.
8. Click **Done editing**.

## Option 2: add a chart while saving it

1. In the chart editor, click **Save**.
2. In **Add to dashboard**, type the name of an existing dashboard and pick
   it, or type a new name and pick it to create a new dashboard. The window
   then says "A new dashboard will be created".
3. Click **Save**. Nova confirms, for example "Dashboard [name] just got
   created and chart [name] was added to it".

## Option 3: add a chart from an agent's answer

Use **Add to Dataviz** on a chart in an answer. See
[Add an answer to Dataviz](/docs/studio/add-to-dataviz).

## Open and change a dashboard

- In **Dataviz**, click **Dashboards** in the top bar and click a dashboard's
  name. The list shows each dashboard's **Status** (**Published** or
  **Draft**), **Owners** and **Last modified**.
- Click **Edit dashboard** to rearrange or add charts, then **Save**.
- The refresh button next to the title (**Refresh dashboard**) loads every
  chart again.

## Delete a dashboard

If you made the dashboard from **Artifacts**, open it there and click
**Delete**, then confirm. This also removes it from Dataviz.

## Limits

- A dashboard made in Dataviz or the BI page is not listed under Studio's
  **Artifacts**; it is listed in Dataviz under **Dashboards**.
- Only a dashboard's owners can edit it. See
  [Who sees what](/docs/dataviz/who-sees-what).

## When another change finishes first

If adding a result reports a conflict, refresh the dashboard and review its
current charts before trying again. Nova keeps the saved publication outcome;
repeating the same completed request does not add another copy. Continue a
pending publication with the same active role that started it.
