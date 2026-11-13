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
engine nodes and live health signals from Nova's services.

## Before you start

Use an administrator role, such as `ACCOUNTADMIN`.

## Open the page

In the left menu, under **Operations** > **Monitoring**, click **Cluster
Monitor**. The page refreshes itself every 15 seconds; **Refresh** updates it at
once.

## What the page shows

| Section | What it tells you |
| --- | --- |
| **Nova services** | **Redis** answers a connection check; **Nova scheduler** has a current leadership lease; **Nova worker** has a recent heartbeat. **All healthy** means all three signals are current. |
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
- An unhealthy **Nova scheduler** has no current leadership lease. An unhealthy
  **Nova worker** has no recent heartbeat. **Unknown** means Nova could not read
  that signal; it does not confirm that the process stopped. These indicators
  do not prove that a particular task completed. Check its run history and
  [Production Health](/docs/monitoring/production-health) for the alert rules.
