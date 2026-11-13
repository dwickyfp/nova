---
title: Multi-statement and Python tasks
description: Run several SQL statements in order, or a Python script with novaduck, as one task.
---

## Several statements in one task

A task can run more than one SQL statement. Put the statements between
`BEGIN` and `END` (or between `$$` and `$$`), and end **each** statement with a
semicolon. Nova runs them in order, one after the other.

```sql
CREATE TASK close_and_total AS BEGIN
  UPDATE orders SET status = 'closed' WHERE amount >= 100;
  INSERT INTO daily_totals SELECT status, COUNT(*), SUM(amount) FROM orders GROUP BY status;
END;
```

The same with `$$`:

```sql
CREATE TASK dollar_block AS $$
INSERT INTO step_log SELECT 'dollar_1', NOW();
INSERT INTO step_log SELECT 'dollar_2', NOW();
$$;
```

### How it runs

- Each run uses the same checks as a statement you type in a worksheet:
  your role's permissions, data policies, and `@stage` names all apply.
- When a run succeeds, the run's step shows a summary such as
  `2 statements ran; 4 rows affected`.
- If a statement fails, Nova stops there. Statements that already ran are
  **kept**, not undone. The step's error says which statement failed, for
  example:

  ```text
  Statement 2 of 3 failed: SQL error: (1064) Getting analyzing error. Detail message: Table no_such_table is not found. (statement 1 ran and is kept)
  ```

### Confirmation

A block that updates, deletes or drops data asks you to confirm **once, when
you create the task**, in the same way as running such a statement in a
worksheet. Scheduled runs do not ask again.

### What a block cannot do

A block cannot create, drop or run tasks, switch roles, or start and end
transactions. For example:

| You write | You get |
| --- | --- |
| `BEGIN CREATE TASK x AS ...; END` | `statement 1: a task cannot create tasks` |
| `BEGIN DROP TASK values_task; END` | `statement 1: a task cannot drop tasks` |
| `BEGIN EXECUTE TASK values_task; END` | `statement 1: a task cannot run other tasks` |
| `$$ INSERT ...; COMMIT; $$` | `statement 2: a task script cannot commit a transaction` |
| `BEGIN USE ROLE ...; END` | `Invalid SQL ...` |

## Python tasks

A Python task runs a Python script each time. Each run gets its own fresh
Python runtime, the same kind a notebook in **Workspaces** uses, with
**novaduck** ready to read your data.

```sql
CREATE TASK py_summary LANGUAGE PYTHON AS $$
import novaduck
con = novaduck.connect()
df = con.sql("SELECT status, COUNT(*) AS n, SUM(amount) AS total FROM nova.DOCS_QA_TASK.orders GROUP BY status ORDER BY status").df()
print(df.to_string(index=False))
$$;
```

- `novaduck.connect()` connects to every catalog your role can see. Nova
  tables are named `nova.<database>.<table>`. The examples on this page use a
  database called `DOCS_QA_TASK`; use your own database name.
- What the script prints is kept with the run. Open the task, click the run in
  **Run history**, and read it under **Output**. The run above printed:

  ```text
  status  n  total
  closed  2  420.0
    open  1   75.5
  ```

- To see the code of a Python task, open the task, select its box in the
  diagram, and click **Python** in the top-right corner.

### Write results back

After `con = novaduck.connect()` the script writes with ordinary SQL:
`con.sql("INSERT INTO nova.<database>.<table> SELECT * FROM df")` inserts the
rows of a pandas DataFrame named `df`. On a table with a primary key, a row
whose key already exists is replaced, so running the task again updates the
rows instead of adding copies. The table must already exist. The write uses the
run's permissions, like a statement you type yourself.

### Packages and Python version

pandas, numpy, pyarrow, polars and DuckDB are already installed. To use another
package, list it in `PACKAGES`. Only packages on your deployment's approved
list can be used; Nova installs them at the start of each run. `RUNTIME_VERSION`
picks the Python version; `3.12` is the one available.

This task counts rows, writes the counts to the `step_counts` table, and prints
them as a table with the `tabulate` package:

```sql
CREATE TABLE step_counts (step VARCHAR(30), runs BIGINT)
  PRIMARY KEY(step) DISTRIBUTED BY HASH(step) BUCKETS 1
  PROPERTIES('replication_num'='1');

CREATE TASK py_write
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.12'
  PACKAGES = ('tabulate')
AS $$
import novaduck
from tabulate import tabulate
con = novaduck.connect()
df = con.sql("SELECT step, COUNT(*) AS runs FROM nova.DOCS_QA_TASK.step_log GROUP BY step ORDER BY step").df()
con.sql("INSERT INTO nova.DOCS_QA_TASK.step_counts SELECT * FROM df")
print(tabulate(df, headers='keys', showindex=False))
$$;

EXECUTE TASK py_write;
```

The run's **Output** starts like this:

```text
[nova] installed tabulate 0.10.0
step        runs
--------  ------
clean_a        1
clean_b        1
dollar_1       1
```

If the target table does not exist, the run fails with an error such as
`CatalogException: Catalog Error: Table with name step_counts does not exist!`
followed by the Python traceback.

### Who the script runs as

- A manual run (**Run** or `EXECUTE TASK`) reads data as you, with the task's
  owner role. Keep that role active until the run ends.
- A scheduled run uses the scheduling account connected to the owner role.

Starting the runtime takes a few seconds, so a Python task takes longer than
a SQL task that does the same work.

## Limits and errors

| Message | What it means |
| --- | --- |
| `'requests' is not on this deployment's approved package list. Ask an administrator to approve it.` | The package is not approved. Packages are checked when you create the task and again at every run. |
| `RUNTIME_VERSION '3.9' is not available; use 3.12` | Use `RUNTIME_VERSION = '3.12'` or leave it out. |
| `The Python runtime could not start: ...` | The runtime service did not start. Run the task again; if it keeps failing, tell your administrator. |
| A step is `abandoned` and its run is `running` | The outcome could not be confirmed. Earlier statements may have committed. Nova does not repeat the step. When the step can no longer be making changes, the run ends `failed` (see [Run states](/docs/tasks/manage-and-monitor-tasks)). Check whether its changes were applied before running the task again. |
