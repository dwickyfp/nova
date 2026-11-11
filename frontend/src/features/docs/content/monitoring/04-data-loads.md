---
title: Data Loads
description: Follow load jobs into Nova tables, see their progress, and find out why a load failed.
---

## What it is

A **load** is a job that writes rows into a table, for example an `INSERT`, a
file copied from a stage, or rows written by a task or stream. The **Data
Loads** page lists recent load jobs and their result.

## Before you start

Use an administrator role, such as `ACCOUNTADMIN`.

## Open the page

In the left menu, under **Operations** > **Monitoring**, click **Data Loads**.

## Read the list

Each row is one load:

| Column | Meaning |
| --- | --- |
| **Label** | The load's unique name. |
| **Database** and **Table** | Where the rows went. |
| **Type** | How the rows came in, such as `INSERT`. |
| **Status** | `FINISHED`, `LOADING`, or `CANCELLED`. |
| **Progress** | How far the load got, for example `ETL:100%; LOAD:100%`. |
| **Scan Rows** and **Sink Rows** | Rows read, and rows written to the table. |
| **Created** and **Duration** | When it started and how long it took. |
| **Error** | A short error, if the load failed. |

## Find a load

1. Type in **Search label, table, error...**.
2. Use the **State** filter (**FINISHED**, **CANCELLED**, **LOADING**), the
   **Database** filter, and the **Type** filter. **Clear filters** removes
   them.
3. Click a row for the **Full Label**, the **Load Start**, **Commit Time**, and
   **Finish Time**, and the full **Error Message**.
