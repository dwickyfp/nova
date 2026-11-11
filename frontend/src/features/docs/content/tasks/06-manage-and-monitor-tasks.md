---
title: Manage and monitor tasks
description: Find your tasks, run, resume or suspend them, read their run history and errors, and remove them.
---

## The Tasks page

Open **Tasks** in the sidebar, under **Data Management**. Each row is one task,
or one [task graph](/docs/tasks/task-graphs) named after its first task.

| Column | What it shows |
| --- | --- |
| **Name** | The task name, its owner role underneath, and a badge with the state of its last run (`success`, `failed`, `running`, `pending`, or `never run`). A scheduled task that is not started also shows `suspended`. |
| **Total Run**, **Success**, **Failed** | How many runs the task has had, and how they ended. |
| **Schedule** | `Manual`, `Interval · INTERVAL 1 MINUTE`, `Cron · 0 3 * * *`, or `On stream data`. |
| **Database** | The database and schema the task lives in. |
| **Created**, **Last run** | When the task was created and when its last run started. |
| **Action** | Buttons for the task (see below). |

Use the search box above the table to filter by task name, database, schedule
or owner role.

### Buttons on a row

- **Run** starts a run now, the same as `EXECUTE TASK`.
- **Resume** or **Suspend** appears on scheduled and stream-triggered tasks.
  **Resume** starts the schedule; **Suspend** stops new runs (a run in progress
  finishes).
- **View error** appears when the last run failed. It opens **Run error**,
  with the message of each step that failed.

## A task's detail page

Click a row to open the task.

1. The top shows the task name, how many tasks the graph has, its schedule,
   and the state of its last run.
2. The middle shows each task as a box, with arrows for the order they run in.
   Click a box, then click **SQL** (or **Python** for a Python task) in the
   top-right corner to read its code.
3. The **Run history** panel at the bottom lists runs, newest first, with
   **Started**, **Finished**, **State**, **Trigger** (`manual`, `schedule` or
   `stream`) and **Duration**. Drag its top edge to make it taller, or click the
   arrow to fold it away.
4. Click a run to see each step: its name, state, attempt, start and finish
   times, and duration. It also shows who the run ran as (for example
   `Executed as nova_admin · ACCOUNTADMIN`), the **Error** of a failed step, and
   the **Output** of a multi-statement or Python step.

The **Tasks** list refreshes by itself while a run is pending or running.

## Manage tasks with SQL

List the tasks in the current database, or in a named one:

```sql
SHOW TASKS;
SHOW TASKS IN DOCS_QA_TASK.default;
```

Each row has the task's `name`, `database_name`, `schema_name`, `state`
(`STARTED` or `SUSPENDED`), `schedule_kind`, `schedule`, `when`, `language`,
`owner_role`, `last_run_state` and `created_at`.

See everything about one task:

```sql
DESCRIBE TASK log_orders;
```

The result is a list of properties: state, schedule, time zone, condition,
language, owner role, who created it and when, overlap policy, graph, last run,
and the task's code (`definition`). For a stream-triggered task it also shows
each stream's position, for example `offset 3, head 5 (has data)`.

Run, start and stop:

```sql
EXECUTE TASK log_orders;
ALTER TASK log_orders RESUME;
ALTER TASK log_orders SUSPEND;
```

Remove a task:

```sql
DROP TASK log_orders;
DROP TASK IF EXISTS log_orders;
```

`DROP TASK` asks you to confirm. The task's run history is kept.

## Run states

| State | Meaning |
| --- | --- |
| `pending` | The run is waiting to start, for example behind a running run with overlap policy `queue`. |
| `running` | The run is in progress. |
| `success` | Every step succeeded or was skipped. |
| `failed` | A step failed. Steps after it were skipped. |

A single step can also be `skipped`: its `WHEN` condition was false, or a step
before it failed.

## Failures and retries

Nova does **not** retry a failed run by itself. Read the error, fix the cause,
and run the task again (or wait for its next scheduled start).

Common errors:

| Error | What to do |
| --- | --- |
| `Run has no execution identity; start a new manual run or configure the owner role's scheduled execution account` | A scheduled or stream-triggered run has no scheduling account for its owner role. Ask an administrator to set one up. Manual runs still work. |
| `This task already has an active run` | The previous run has not finished and the overlap policy is `skip`. Wait for it, or use `OVERLAP_POLICY = queue`. |
| `Statement 2 of 3 failed: ... (statement 1 ran and is kept)` | A statement in a multi-statement task failed. Earlier statements were not undone. |
| `Task ... has a run in progress; drop it after the run ends` | `DROP TASK` waits for the current run to end. |
| `Tasks clean_a, clean_b, notify_done run after or finalize load_raw; drop them first` | Drop the tasks that depend on this one first. |
| `The worker stopped while this step was running, so Nova cannot tell whether its changes were applied. ...` | The run was interrupted. Nova does not rerun it on its own, so nothing is written twice. Check the data, then run the task again. |

## Who can see and run a task

The **Tasks** page, `SHOW TASKS` and `DESCRIBE TASK` show the tasks owned by
your **active role**. To see, run, resume, suspend or drop a task owned by
another of your roles, switch to that role first.
