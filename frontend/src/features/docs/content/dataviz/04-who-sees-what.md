---
title: Who sees what
description: Understand who can open a native dashboard, and why two people can see different numbers on the same dashboard.
---

## What it is

Two separate rules decide what you see in Dataviz:

| Rule | What it controls |
| --- | --- |
| The dashboard's **sharing** | Whether you can open, edit or publish the dashboard. |
| Your own **data access** | Which rows and values each chart shows you. |

Sharing never widens data access. Every chart runs its query with **your**
sign-in and **your** active role, under your row filters and column masks.
Two people who open the same dashboard can see different numbers when their
roles read different rows.

## Personal and shared workspaces

- A dashboard in your **Personal** workspace is private. Only you can open it.
- A dashboard in a **shared workspace** can be shared with people or roles from
  **Share**. See [Build a native dashboard](/docs/dataviz/native-dashboards#publish-and-share).
- You choose the workspace when you click **New dashboard**.

## Access levels

| Access | What it allows |
| --- | --- |
| **Can view** | Open the published version and use filters and selections. |
| **Can edit** | Change the draft: datasets, charts, layout and filters. |
| **Can edit and publish** | Also publish a new version for viewers. |
| **Can manage** | Everything, including sharing. |
| **Export** | Added separately when someone may export. |

The person who creates a dashboard owns it and always has full access.

## Drafts and published versions

Editors work on a draft. Viewers see the published version only, including the
exact chart and dataset versions it uses. Publishing again replaces what
viewers see; the draft history is kept in **Version history**.

## Switching roles

Switch your active role from your name at the bottom of Studio's rail
(**Switch role**). Every chart then runs again under that role's data access.
A role you share a dashboard with only matters while it is your active role.

## Limits

- If your role cannot read a table a chart uses, that chart says the data is
  unavailable with your current role; the rest of the dashboard still loads.
