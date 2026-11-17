# Module 23: Charts and dashboards

> Save answers as charts, lay them out on dashboards, and keep both live under
> each viewer's own access.

Saved charts, dashboards and their datasets are native Nova documents, built
in Studio's **Dataviz** tab. The engine is described in
[Architecture 19](arch-19-studio-dataviz.md) and the
[native visualization guide](../backend/app/modules/viz/AGENTS.md). Apache
Superset, the earlier BI tool, was removed on 2026-11-16
([Architecture 17](arch-17-superset.md), historical).

---

## Building a dashboard

- **In Dataviz:** open Studio, click **Dataviz**, then **New dashboard**. Add a
  dataset on the **Data** tab, then add charts, KPIs, tables and text on the
  **Canvas** tab.
- **From Neva:** the Dataviz assistant explains a dashboard and, with your
  approval, changes it for you.

A dataset is a table, view, SQL query or Semantic View. Charts read datasets
of their dashboard, and dashboard filters apply to every chart on the same
dataset.

## Who sees what

Every chart query runs with the viewer's sign-in and active role, through
Nova's SQL path, so Ranger row filters and masks apply exactly as in a SQL
worksheet. Two people opening the same dashboard can see different rows.

Each saved change is a new revision of the draft. Viewers see only the
published revision; later edits stay in the draft until it is published again.

The step-by-step user guide is in the product's `/docs` under **Dataviz**.
