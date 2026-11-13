# nova-scheduler

The scheduler decides when a task graph runs. A PostgreSQL leader lease fences
occurrence reservations. One transaction stores the occurrence, graph run,
immutable `task.graph` job and outbox event. Redis carries wakeups; losing a
notification does not erase the job. Task workers own execution.

## Run

`./dev.sh` starts the scheduler with the other development roles. To supervise
it separately, run from `backend/`:

```bash
uv run python -m app.scheduler
```

The process requires the scheduler PG login, an installed schema and published
epoch, plus StarRocks and Redis. Startup probes authority and refuses schema or
epoch mismatches; it never migrates or imports metadata. The engine pool serves
timezone observations and Stream maintenance. It does not own control metadata.

## Scheduling and leadership

`build_scheduler_service` creates one `MetadataLeaderLock` and passes it to both
the polling service and `SchedulerTick`. Each reservation locks that live lease
in PG and checks its holder, token and expiry. An expired leader cannot commit a
new reservation. The polling loop renews the lease during a tick and cancels
work if renewal fails; shutdown releases the lease.

A graph run uses a deterministic ID for its root and due instant. Repeating a
reservation returns its existing job without resetting run state. The scheduler
may send an immediate Redis wakeup after commit; the outbox publisher recovers
an unsent or unacknowledged notification. Workers claim durable work before
executing it. Lease loss does not authorize replay of an uncertain external
mutation.

- Cron schedules use the task's explicit timezone.
- Interval schedules start one interval after creation. PG timestamps preserve
  the instant; historical naive engine values require reviewed clock provenance.
- Manual tasks do not fire on a schedule.
- Scheduled tasks with `WHEN` require both the due occurrence and a true
  condition. A skip does not advance a Stream consumption offset.
- Only graph roots anchor schedules. A graph with several scheduled roots has
  a separate occurrence per due root. Catch-up is bounded by the schedule owner.

`SCHEDULER_POLL_INTERVAL_SECONDS` controls polling and
`SCHEDULER_LEADER_LOCK_TTL_SECONDS` controls the PG lease duration (bounded by
`MetadataLeaderLock`). `SCHEDULER_ENGINE_TIMEZONE` is normally empty: the process
reads `@@time_zone` and verifies any explicit override against the engine.
`SCHEDULER_LEADER_LOCK_KEY` belongs to the historical Redis-lock implementation;
it does not select the current PG lease, named `task.scheduler`.

## Owners and validation

| File | Responsibility |
| --- | --- |
| `app/modules/task_orchestration/schedule.py` | Pure schedule parsing and occurrence calculation |
| `app/modules/task_orchestration/scheduler.py` | Plan due graphs, apply overlap rules and reserve work |
| `app/modules/task_orchestration/leadership.py` | PG lease acquisition, renewal and release |
| `app/modules/task_orchestration/metadata_scheduler.py` | Fenced occurrence reservation |
| `app/modules/task_orchestration/repository.py` | Actor-bound graph/job transaction |
| `app/modules/task_orchestration/service.py` | Polling, lease renewal and production factory |
| `app/scheduler/__main__.py` | Process configuration, dependency admission and shutdown |

Focused unit checks run without containers:

```bash
uv run pytest tests/unit/test_task_schedule.py \
  tests/unit/test_task_scheduler_tick.py \
  tests/unit/test_task_scheduler_leader_lock.py \
  tests/unit/test_metadata_scheduler.py tests/unit/test_entrypoints_failclosed.py
```

The real PG suites `test_metadata_tasks.py` and `test_metadata_scheduler.py`
cover the production factory, contention, handover, duplicate reservations and
expiry. Run them only on an acknowledged disposable target; their fixtures
replace metadata authority. Use CI's governed engine setup for engine integration,
never the PG database of a running or cut-over development deployment. Skips are
not passes. See the [metadata guide](../../metadata_migrations/AGENTS.md) and
[runtime guide](../worker/AGENTS.md) for the complete gates.
