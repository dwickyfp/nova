---
title: Create a task
description: Save a SQL statement as a task, run it once, and check that it worked.
---

## What you will do

You will save an `INSERT` statement as a task with `CREATE TASK`, run it with
`EXECUTE TASK`, and look at the result on the **Tasks** page.

## Before you start

- [Prepare the task examples](/docs/tasks/example-tables) if you want to copy
  the SQL on this page. It creates `orders` and `order_log`.
- You need a SQL worksheet. Open **Workspaces** in the sidebar and open or
  create a SQL worksheet.
- Your active role needs permission to write to the table the task fills.
  The task will run with this role (its **owner role**).
- A task lives in a database. Either choose the database in the database
  selector at the top of the worksheet, or give the task a full name:
  `database.schema.task_name`. Nova databases have one schema, `default`.

## Steps

1. In the worksheet, choose the database that holds your tables.
2. Type a `CREATE TASK` statement. This one adds a row to `order_log` with
   the number of orders and their total each time it runs:

   ```sql
   CREATE TASK log_orders
   AS INSERT INTO order_log SELECT NOW(), COUNT(*), SUM(amount) FROM orders;
   ```

3. Run the statement. The result shows the new task: its name, database,
   schema, schedule and overlap policy. A note under the result says the task
   was saved and has not run yet.
4. Run the task once:

   ```sql
   EXECUTE TASK log_orders;
   ```

   The result shows the run with state `pending` and trigger `manual`, and the
   note "Task run queued. Follow its progress in Tasks."
5. Open **Tasks** in the sidebar. Find `log_orders` in the list. After a few
   seconds the badge next to its name changes to `success` (or `failed`), and
   **Total Run** and **Success** count the run.
6. Click the row to open the task. The **Run history** panel at the bottom
   lists the run. Click the run to see each step, its state, who it ran as,
   and how long it took.
7. Check the table the task wrote to:

   ```sql
   SELECT * FROM order_log;
   ```

You can also run a task from the **Tasks** page with the **Run** button on its
row. It does the same as `EXECUTE TASK`.

## What a task body can be

The part after `AS` is the **body**. It can be:

| Body | Example |
| --- | --- |
| One `INSERT ... SELECT` | `AS INSERT INTO order_log SELECT NOW(), COUNT(*), SUM(amount) FROM orders` |
| One `INSERT ... VALUES` | `AS INSERT INTO step_log VALUES ('values', NOW())` |
| Several statements run in order | `AS BEGIN UPDATE ...; INSERT ...; END` (see [Multi-statement and Python tasks](/docs/tasks/multi-statement-and-python-tasks)) |
| A Python script | `LANGUAGE PYTHON AS $$ ... $$` (see the same page) |

A body that is only a `SELECT` is refused, because a task has nowhere to show
results. Write the results into a table with `INSERT INTO ... SELECT`.

## Change a task

`ALTER TASK` only resumes and suspends tasks. To change what a task does,
drop it and create it again:

```sql
DROP TASK log_orders;
CREATE TASK log_orders
AS INSERT INTO order_log SELECT NOW(), COUNT(*), SUM(amount) FROM orders;
```

`DROP TASK` asks you to confirm. The run history of the old task is kept and
shows under the new task with the same name.

## Limits and errors

| Message | What it means |
| --- | --- |
| `CREATE TASK needs a database: select one, or name the task database.schema.task` | No database is selected. Choose one in the worksheet, or use the full name, for example `CREATE TASK sales.default.log_orders ...`. |
| `Task <name> already exists; drop it first with DROP TASK` | Task names are unique within a database. Drop the old task or pick another name. |
| `Invalid SQL: ... Unexpected or missing SQL token near 'SELECT'` | The body is a bare `SELECT`. Use `INSERT INTO ... SELECT`. |

> **Note:** Nova checks the task's text when you create it, but tables are
> checked when it runs. A task that writes to a table that does not exist is
> saved, and its run fails with "Table ... is not found".
