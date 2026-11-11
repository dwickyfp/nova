---
title: Monitoring overview
description: Know which Monitoring page answers which question, from AI usage to cluster health.
---

## What it is

The **Monitoring** pages show what Nova and its query engine are doing: who
runs what, how long it takes, what data is loading, how much AI is used, and
whether the system is healthy. Looking at them never changes anything. The
only actions that do are **Kill** on a running query and the approval buttons
on the **Autopilot** tab of **Queries**.

## Before you start

Monitoring is meant for administrators. Use an administrator role, such as
`ACCOUNTADMIN`.

## Open Monitoring

In the left menu, under **Operations**, click **Monitoring**. The group opens
with these pages:

| Page | Question it answers | Read more |
| --- | --- | --- |
| **AI Monitoring** | How many AI tokens are we using, by whom, and for what? | [AI Monitoring](/docs/monitoring/ai-monitoring) |
| **Queries** | What runs now, what ran, what did it cost, and how can it get faster? | [Queries](/docs/monitoring/queries) |
| **Audit Trail** | Who did what, and when? | [Audit trail](/docs/access-control/audit) |
| **Data Loads** | Which loads finished, which are running, and which failed? | [Data Loads](/docs/monitoring/data-loads) |
| **Cluster Monitor** | Are the engine's nodes and Nova's background services up? | [Cluster Monitor](/docs/monitoring/cluster-monitor) |
| **Production Health** | Are any alerts firing, and is this cluster ready for production? | [Production Health](/docs/monitoring/production-health) |

Every page has a **Refresh** button or refreshes itself. Tables can be
searched and filtered, and most rows open to show their full details.

## Times

The AI Monitoring page uses UTC dates. The other pages show times as the engine
records them.
