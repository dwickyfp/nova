---
title: The BI page
description: Open Nova's BI tool as a full page from the Console, and know how it relates to Dataviz.
---

## What it is

The **BI** page shows the same BI tool as Studio's Dataviz tab, as a normal
Console page: dashboards, charts, datasets and SQL on governed Nova data. Use
it when you want BI without leaving the Console. The Dataviz tab adds the
Dataviz assistant beside it.

The dashboards and charts are the same in both places, and the same rules
apply (see [Who sees what](/docs/dataviz/who-sees-what)).

## Open the BI page

1. In the Console's left menu, click **BI**.
2. The page header says **BI** and "Dashboards, charts and SQL on governed
   Nova data". The BI home page loads below it.

Header buttons:

| Button | What it does |
| --- | --- |
| Refresh (circular arrow) | Reloads the BI view. |
| **Open in new tab** | Opens the same view in its own browser tab. |

## Find your way around

The BI tool's top bar has:

| Item | What it lists |
| --- | --- |
| **Dashboards** | All dashboards you can see, with **Status**, **Owners** and **Last modified**, and filters for name, status, owner, favourite and certification. **+ Dashboard** creates one. |
| **Charts** | All charts you can see. |
| **Datasets** | The queries that charts read from. |
| **SQL** | A SQL editor on the Nova database. |

The home page also shows **Recents**, your **Dashboards** and your **Charts**,
with **Favorite**, **Mine** and **All** tabs. Turn **Thumbnails** off to list
them without preview pictures.

The address in your browser follows what you open, so you can bookmark or
share a link to a dashboard. The person who opens the link still sees only what
their own role allows.

## Limits

- Connecting databases and uploading files to a database are not available in
  BI. Load data through Nova (stages, connections and SQL) instead.
- Importing dashboard bundles is not available.
- Charts run their queries through Nova, so the same row filters and masks
  apply as in a SQL worksheet.
