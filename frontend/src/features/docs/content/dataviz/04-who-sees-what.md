---
title: Who sees what
description: Understand how Dataviz dashboards belong to a Nova role, and why two people can see different numbers on the same dashboard.
---

## What it is

Dataviz dashboards are not shared person to person. Instead, **a dashboard
belongs to the active role of the person who created it**. Everyone who works
under that role can open it in Dataviz.

Two separate rules decide what you see:

| Rule | What it controls |
| --- | --- |
| The dashboard's **role** | Whether the dashboard appears for you at all. |
| Your own **data access** | Which rows and values each chart shows you. |

The role never widens data access. When you open a dashboard, every chart
runs its query with **your** sign-in and **your** active role, under your
row filters and column masks. Two people in the same role with different row
filters can see different numbers on the same dashboard.

## How a dashboard gets its role

When you create a dashboard (from **Artifacts**, from the chart editor's
**Add to dashboard**, from **Add to Dataviz**, or in the BI tool itself), Nova:

1. attaches it to your current active role, shown in BI as
   `Nova role <ROLE>` (for example `Nova role ACCOUNTADMIN`);
2. publishes it, so the members of that role can open it at once.

## Owners and editing

- The person who creates a dashboard is its **owner**. Owners always see their
  dashboards, whatever role is active.
- Only owners can edit a dashboard. Other members of the role can view it.
- **Add to Dataviz** lists only dashboards you can edit.

## Switching roles

Switch your active role from your name at the bottom of Studio's rail
(**Switch role**). Dataviz then shows the dashboards of the new role, and
every chart runs again under that role's data access.

## Who may create charts and dashboards

Creating BI content needs an **author** role. `ACCOUNTADMIN` is always one;
an administrator decides which other roles may author. Roles that are not
authors can view dashboards of their role, and do not see **Add to Dataviz**.

If two administrators update author roles at the same time, one update may
report a conflict. Reload the current author list before submitting another change.

## Limits

- There is no "share with a person" for Dataviz dashboards. To give someone a
  dashboard, they need to work under the dashboard's role.
- `ACCOUNTADMIN` does not see dashboards that belong to other roles unless it
  owns them.
