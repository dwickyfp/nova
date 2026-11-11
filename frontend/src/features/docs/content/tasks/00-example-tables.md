---
title: Prepare the task examples
description: Create the small tables used throughout the task and stream guides.
---

## Before you start

Use a practice database where your active role can create tables and change
data. The examples below create `DOCS_QA_TASK`. Choose a different name if that
database already contains work you need to keep, and replace the name in the
later Python and stream-trigger examples too.

## Create the tables

Open a SQL worksheet in **Workspaces** and run this once:

```sql
CREATE DATABASE DOCS_QA_TASK;
USE DOCS_QA_TASK;

CREATE TABLE orders (
  order_id INT NOT NULL,
  customer VARCHAR(100),
  amount DECIMAL(12,2),
  status VARCHAR(20)
) PRIMARY KEY(order_id) DISTRIBUTED BY HASH(order_id);

INSERT INTO orders VALUES
  (1,'Ana',120.00,'open'),
  (2,'Budi',75.50,'open'),
  (3,'Citra',300.00,'closed');

CREATE TABLE order_log (run_at DATETIME, n BIGINT, total DECIMAL(12,2))
  DUPLICATE KEY(run_at) DISTRIBUTED BY RANDOM;
CREATE TABLE daily_totals (status VARCHAR(20), n BIGINT, total DECIMAL(12,2))
  DUPLICATE KEY(status) DISTRIBUTED BY RANDOM;
CREATE TABLE step_log (step VARCHAR(30), at DATETIME)
  DUPLICATE KEY(step) DISTRIBUTED BY RANDOM;
CREATE TABLE order_events (
  order_id INT, amount DECIMAL(12,2), action VARCHAR(10),
  is_update BOOLEAN, row_id VARCHAR(255)
) DUPLICATE KEY(order_id) DISTRIBUTED BY HASH(order_id);
CREATE TABLE order_updates (order_id INT, amount DECIMAL(12,2))
  DUPLICATE KEY(order_id) DISTRIBUTED BY HASH(order_id);
INSERT INTO order_updates VALUES (1,130.00),(5,60.00);
```

Keep `DOCS_QA_TASK` selected when running subsequent examples. Begin with
[Create a task](/docs/tasks/create-a-task).

## Run examples independently

Some pages show alternative definitions with the same task name, such as
`log_orders`. Drop the previous definition before creating its replacement.
Tasks that only demonstrate a schedule can stay suspended.

The stream guide expects the three original order rows above. Run it before
examples that update those rows, or use a fresh practice database. Each
successful task run adds rows to its output tables, so counts grow when you
repeat a run.

## Clean up afterwards

Suspend scheduled or stream-triggered root tasks first. Drop tasks in reverse
dependency order, then drop streams before removing their source tables. Only
remove the practice database when no examples still need it.
