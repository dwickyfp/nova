---
title: Schedules
description: Run a task every few minutes, every hour, or at set times of day, and start or stop its schedule.
---

## What a schedule is

A **schedule** tells Nova when to run a task by itself. There are two kinds:

- an **interval**, such as every 5 minutes or every day;
- a **cron** schedule, such as "every day at 03:00" or "weekdays at 07:30",
  in a time zone you choose.

A task with a schedule still starts **suspended**. Nothing runs until you
resume it.

## Before you start

- Scheduled runs use a scheduling account that an administrator connects to
  the task's owner role. Without it, every scheduled run fails with "Run has no
  execution identity; start a new manual run or configure the owner role's
  scheduled execution account". Ask your administrator to set one up for your
  role. Manual runs do not need it.
- You need a SQL worksheet (**Workspaces** in the sidebar) with your database
  selected.

## Run every few minutes, hours or days

Use `SCHEDULE EVERY (INTERVAL <number> <unit>)`. The unit is `SECOND`,
`MINUTE`, `HOUR` or `DAY`.

```sql
CREATE TASK log_orders
  SCHEDULE EVERY (INTERVAL 1 MINUTE)
AS INSERT INTO order_log SELECT NOW(), COUNT(*), SUM(amount) FROM orders;
```

```sql
CREATE TASK hourly_log
  SCHEDULE EVERY (INTERVAL 1 HOUR)
AS INSERT INTO order_log SELECT NOW(), COUNT(*), SUM(amount) FROM orders;
```

```sql
CREATE TASK daily_log
  SCHEDULE EVERY (INTERVAL 1 DAY)
AS INSERT INTO order_log SELECT NOW(), COUNT(*), SUM(amount) FROM orders;
```

Intervals count from the moment the task was created. Nova checks for due
tasks about every 15 seconds, so a run can start a few seconds after its time.

## Run at set times (cron)

Use `SCHEDULE = 'USING CRON <minute> <hour> <day of month> <month> <day of week> <time zone>'`.
The five fields are standard cron fields; the time zone is a name such as
`Asia/Jakarta`, `Europe/London` or `UTC`.

Every day at 03:00 Jakarta time:

```sql
CREATE TASK nightly_log
  SCHEDULE = 'USING CRON 0 3 * * * Asia/Jakarta'
AS INSERT INTO order_log SELECT NOW(), COUNT(*), SUM(amount) FROM orders;
```

Every day at 06:00 UTC:

```sql
CREATE TASK utc_log
  SCHEDULE = 'USING CRON 0 6 * * * UTC'
AS INSERT INTO order_log SELECT NOW(), COUNT(*), SUM(amount) FROM orders;
```

You can leave out `USING CRON` and the time zone and write only the five
fields. Without a time zone, Nova uses the deployment's default time zone.
`DESCRIBE TASK` shows which one the task uses.

Weekdays (Monday to Friday) at 07:30:

```sql
CREATE TASK weekday_log
  SCHEDULE = '30 7 * * 1-5'
AS INSERT INTO order_log SELECT NOW(), COUNT(*), SUM(amount) FROM orders;
```

Every 15 minutes:

```sql
CREATE TASK notz_log
  SCHEDULE = 'USING CRON */15 * * * *'
AS INSERT INTO order_log SELECT NOW(), COUNT(*), SUM(amount) FROM orders;
```

### Cron fields

| Field | Allowed values | Example |
| --- | --- | --- |
| Minute | 0-59 | `30` |
| Hour | 0-23 | `7` |
| Day of month | 1-31 | `1` (first of the month) |
| Month | 1-12 | `*` (every month) |
| Day of week | 0-6, Sunday is 0 | `1-5` (Monday to Friday) |

`*` means "every", `*/15` means "every 15th", and `1-5` is a range.

## Start and stop a schedule

1. Resume the task so its schedule starts:

   ```sql
   ALTER TASK log_orders RESUME;
   ```

   The result shows the task with state `STARTED`.
2. Check its state at any time with `SHOW TASKS` (the `state` column) or on the
   **Tasks** page, where a suspended scheduled task shows a `suspended` badge.
3. Stop the schedule:

   ```sql
   ALTER TASK log_orders SUSPEND;
   ```

   A run that is already going finishes; no new ones start.

On the **Tasks** page, a scheduled task has a **Resume** or **Suspend** button
on its row that does the same.

## When a run is still going at the next start time

The **overlap policy** decides what happens when a task is due while its
previous run has not finished. Set it with `OVERLAP_POLICY`, before
`SCHEDULE`:

| Policy | What happens |
| --- | --- |
| `skip` (default) | The new run is not started. The task runs again at the next start time after the current run ends. |
| `queue` | The new run waits and starts when the current run ends. |
| `allow` | The new run starts at once, next to the current one. |

```sql
CREATE TASK q_task
  OVERLAP_POLICY = queue
AS INSERT INTO step_log SELECT 'queue', NOW();
```

## Limits and errors

| Message | What it means |
| --- | --- |
| `cron expression must have exactly 5 fields, got 3: 'INTERVAL 1 HOUR'` | An interval was written as a cron string. Use `SCHEDULE EVERY (INTERVAL 1 HOUR)`. |
| `invalid cron expression: '0 99 * * *'` | A field is out of range (here hour 99). |
| `invalid cron expression: 'CRON 0 3 * *'` | `CRON` was written without `USING`. Write `USING CRON 0 3 * * *` or just the five fields. |
| `invalid task timezone: 'Mars/Base'` | The time zone name is not known. Use a name such as `Asia/Jakarta` or `UTC`. |
| `unknown OVERLAP_POLICY 'sometimes'; expected one of allow, queue, skip` | Use one of the three policies. |
| `CREATE TASK clauses must appear in the order AFTER, FINALIZE, WHEN, OVERLAP_POLICY, SCHEDULE, LANGUAGE, RUNTIME_VERSION, PACKAGES` | Reorder the clauses. For example `OVERLAP_POLICY` comes before `SCHEDULE`. |

> **Note:** Only the first task of a [task graph](/docs/tasks/task-graphs)
> follows a schedule. A schedule on a task that runs `AFTER` another task is
> saved but not used; that task runs when its parent finishes.
