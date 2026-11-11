---
title: Cluster Monitor
description: Check that the query engine's nodes and Nova's background services are up and answering.
---

## What it is

Nova's query engine runs on two kinds of machines, called **nodes**:

- **Frontends** receive SQL, plan it, and keep the catalog of databases and
  tables.
- **Backends** store the data and do the heavy work of each query.

Nova also runs background services of its own. The **Cluster Monitor** shows
all of them, read live from the engine.

## Before you start

Use an administrator role, such as `ACCOUNTADMIN`.

## Open the page

In the left menu, under **Operations** > **Monitoring**, click **Cluster
Monitor**. The page refreshes itself every 15 seconds; **Refresh** updates it at
once.

## What the page shows

| Section | What it tells you |
| --- | --- |
| **Nova services** | **Redis** (Nova's fast session and cache store), the **Nova scheduler** (starts scheduled tasks), and the **Nova worker** (runs them). **All healthy** means each one answered. |
| **Frontend health** | Counters from the engine's frontend: **Success rate**, **Failed queries**, **Slow queries**, **Connections**, **Total queries**, and latency (**Avg latency**, **p95 latency**, **p99 latency**). |
| **Frontends** and **Backends** | Each node's **Host**, **Role**, **Port**, **Last heartbeat**, and **State**. |

You can see the same node lists in a worksheet:

```sql
SHOW FRONTENDS;
SHOW BACKENDS;
```

## What to do when something is down

- A node whose **State** is not alive, or whose **Last heartbeat** is old, is
  not answering. Queries that need it fail until it is back.
- If **Nova scheduler** or **Nova worker** is not healthy, scheduled tasks do
  not start. See [Production Health](/docs/monitoring/production-health) for
  the alert rules.
