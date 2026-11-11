---
title: What are tasks?
description: Understand what a Nova task is, how it starts, and whose permissions it runs with.
---

## What a task is

A **task** is a piece of saved work that Nova runs for you: one or more SQL
statements, or a short Python script. You write it once with `CREATE TASK`, and
Nova runs it again whenever it should, without you opening a worksheet.

Typical uses:

- refresh a summary table every night;
- copy new orders into a history table every hour;
- run three steps in a fixed order (load, clean, publish);
- process the rows that changed in a table as soon as they change (with a
  [stream](/docs/tasks/streams)).

## How a task starts

A task starts in one of three ways:

| Start | What it means | How you set it up |
| --- | --- | --- |
| Manually | It runs only when someone runs it. | No `SCHEDULE` and no stream condition. Use **Run** on the Tasks page or `EXECUTE TASK`. |
| On a schedule | It runs at fixed times, such as every hour or every night at 03:00. | Add a `SCHEDULE`. See [Schedules](/docs/tasks/schedules). |
| On stream data | It runs when a table it watches has new changes. | Add `WHEN SYSTEM$STREAM_HAS_DATA(...)`. See [Run a task when data changes](/docs/tasks/stream-triggered-tasks). |

Every task can also be run manually at any time, whatever its schedule.

> **Note:** A new task is created **suspended**. Its schedule or stream
> condition does nothing until you resume it with `ALTER TASK name RESUME` or
> the **Resume** button on the Tasks page. Manual runs work while a task is
> suspended.

## Whose permissions a task uses

A task belongs to the role that was active when you created it (its **owner
role**). Every run uses that role's access, so a task can never read or write
more than its owner role can.

- A **manual run** runs as you, the person who pressed **Run** or typed
  `EXECUTE TASK`, with the task's owner role. Your session must still be
  signed in with that role active until the run ends.
- A **scheduled or stream-triggered run** runs as a dedicated scheduling
  account that an administrator connects to the owner role. If no account is
  connected to the role, scheduled and stream-triggered runs fail with "Run has
  no execution identity; start a new manual run or configure the owner role's
  scheduled execution account". Ask your administrator to set one up for the
  role.

## Where to see tasks

Open **Tasks** in the sidebar, under **Data Management**. The page lists every
task you can see, with how many times it ran, how many runs succeeded or
failed, its schedule, and when it last ran. Open a task to see its steps as a
diagram and its run history. See
[Manage and monitor tasks](/docs/tasks/manage-and-monitor-tasks).

## What's next

- [Create a task](/docs/tasks/create-a-task)
- [Schedules](/docs/tasks/schedules)
- [Multi-statement and Python tasks](/docs/tasks/multi-statement-and-python-tasks)
- [Task graphs](/docs/tasks/task-graphs)
- [Streams](/docs/tasks/streams)
