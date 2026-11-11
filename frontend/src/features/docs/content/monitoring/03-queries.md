---
title: Queries
description: See the SQL running right now, search past queries, follow query volume and errors, and review Autopilot suggestions.
---

## What it is

The **Queries** page collects every view of SQL work in one place, in four
tabs:

| Tab | What it shows |
| --- | --- |
| **Running now** | Statements the engine is running at this moment. |
| **History** | Statements that already ran through Nova, newest first. |
| **Cost** | Query counts, errors, slow queries, and duration over time. |
| **Autopilot** | Workload changes Nova has noticed, and suggested improvements. |

## Before you start

Use an administrator role, such as `ACCOUNTADMIN`.

## Open the page

In the left menu, under **Operations** > **Monitoring**, click **Queries**.

## Running now

The table lists each connection's **User**, **Database**, **Command**,
**Query**, **State**, and **Time** (seconds). It refreshes every 5 seconds.

- Filter with **Search query, host, state...** and the **User** and
  **Database** filters.
- Click a row to see the **Full Query**, **Connection ID**, **Host**,
  **Query ID**, and **Current State**.

### Stop a query

1. In the query's row, click **Kill**.
2. Read the **Kill Query** confirmation, which shows the connection, user, and
   query.
3. Confirm with **Kill**.

Nova shows *Query killed successfully*. This cannot be undone; the person who
ran the query gets an error.

> **Note:** The list also shows the connections Nova itself uses for its own
> bookkeeping. Kill only queries you recognize.

## History

The table lists each statement's **Time**, **User**, **Database**, **SQL**,
**Status**, **Duration**, and **Rows**.

- Filter with **Search SQL...**, **Status** (**SUCCESS** or **ERROR**),
  **User**, and **Database**. **Clear filters** removes them.
- Scroll down to load older statements.
- Click a row for the **Full SQL Query**, the **Error Message**, the **Query
  ID**, **Schema**, **Session**, and **File ID** (the worksheet it came from).

**Ask Neva** opens the assistant, for example to explain why a query failed.

## Cost

The top of the tab shows **Total Queries**, **Success Rate**, **Error Count**,
**Slow Queries**, and **Active Connections**. The chart **Query Volume &
Duration Over Time** groups queries **By Hour** or **By Day**. Below it, a table
lists queries with **User**, **Database**, **SQL**, **Status**, **Duration**,
and rows affected; click a row for the full SQL.

## Autopilot

**Query Autopilot** watches the workload for queries that became slower than
usual, and can test and apply improvements such as refreshing table statistics.

- **Operating mode** decides how much Autopilot may do: **Observe** (record
  only), **Governed**, or **Autonomous**. Higher-risk changes need approval in
  every mode.
- Each record can be opened to see the evidence, the proposed change, and the
  buttons to **Run sandbox experiment**, **Approve this version**, **Apply
  validated change**, or **Reject**.

Autopilot needs enough history before it reports a slowdown: it compares
30-minute windows against up to 14 days of past runs.
