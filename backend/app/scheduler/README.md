# nova-scheduler

The singleton process that decides **when** a task graph runs. It computes
cron/interval next-fire times, writes a `CONFIG_TASK_GRAPH_RUNS` row to
`NOVA_SYSTEM`, then pushes a job to a Redis Stream for the workers.

It **never executes SQL against StarRocks**. That separation — "decide" vs "do" —
is what lets the scheduler be a singleton while `nova-worker` scales
horizontally.

## How to run it

```bash
cd backend
uv run python -m app.scheduler
```

It is a separate process, **not** embedded in the FastAPI lifespan (unlike the
MySQL proxy). It initialises the system connection pool, connects to Redis, and
ticks until `SIGINT`/`SIGTERM`.

Prerequisites: StarRocks up with the Phase 9 `CONFIG_TASK*` tables created, and
Redis reachable at `REDIS_URL`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis for the stream and the leader lock |
| `SCHEDULER_POLL_INTERVAL_SECONDS` | `15` | Seconds between ticks |
| `SCHEDULER_LEADER_LOCK_KEY` | `nova:scheduler:leader` | Leader-lock key; one holder ticks |
| `SCHEDULER_LEADER_LOCK_TTL_SECONDS` | `60` | Lock TTL, renewed each tick |
| `SCHEDULER_ENGINE_TIMEZONE` | `Asia/Jakarta` | StarRocks session timezone for `NOW()` reads |
| `TASK_STREAM_KEY` | `nova:tasks:graph_runs` | Redis Stream the scheduler `XADD`s to |
| `TASK_STREAM_GROUP` | `nova-workers` | Consumer group the workers read with |
| `TASK_STREAM_MAXLEN` | `10000` | Approximate stream trim length |

`SCHEDULER_ENGINE_TIMEZONE` must match the session timezone of the scheduler's
StarRocks connection: Nova writes `created_at` with `NOW()` in that zone and reads
it back naive, so the scheduler is told what the wall-clock means rather than
silently assuming UTC. The default is `Asia/Jakarta`, the zone this project's
engine runs in (`docker/docker-compose-engine.yml` sets `TZ=Asia/Jakarta`).

At startup the scheduler reads `SELECT @@time_zone` and **refuses to start** if it
disagrees with `SCHEDULER_ENGINE_TIMEZONE` (a fixed offset like `+07:00` is
accepted as a synonym for the matching IANA zone). A mismatch would shift every
interval anchor by the offset difference, so tasks would never become due; failing
loudly is the point.

## What a tick does

```
list tasks + edges
  → for each graph, find root tasks with a due occurrence
  → per due root: deterministic run id = uuid5(graph_id, due instant)
      if the row already exists: skip          (idempotent)
      else: INSERT CONFIG_TASK_GRAPH_RUNS      (persist FIRST)
            XADD nova:tasks:graph_runs         (publish SECOND)
```

**Ordering is the guarantee.** The row is written before the push, so a Redis
flush or a failed `XADD` loses no work — the reconciler re-derives it. Redis is
ephemeral transport; `NOVA_SYSTEM` is the source of truth.

**Idempotency is the key.** The run id is a UUID v5 of `graph_id` + due instant,
so two ticks for the same due time resolve to the same primary key and produce
one run, not two.

**A graph is one run.** Only root tasks (no incoming edge) anchor a schedule; a
due root creates a single graph run covering every node reachable from it. A
`A → B → [C, D]` graph is one run, not four.

## Schedule semantics

* **`cron`** — 5-field expression, evaluated in the task's explicit IANA
  timezone. `0 2 * * * Asia/Jakarta`-style wall-clock alignment needs the
  timezone to be stored per task (StarRocks `START` literals use the session
  zone), which is why `timezone` is mandatory and never defaulted.
* **`interval`** — `EVERY(INTERVAL n UNIT)`. Occurrences are anchored to the
  task's creation time; the first fire is one interval later, never at the
  creation instant. The anchor is read from `created_at` and interpreted in
  `SCHEDULER_ENGINE_TIMEZONE`.
* **`manual`** — never fires on a schedule.

A task created after today's cron occurrence does not fire until the next one; a
tick that arrives late fires once per elapsed occurrence, not once per tick.

## Leader lock

`SET key token NX EX ttl`, with compare-and-set Lua scripts for renew and
release so a holder whose TTL lapsed cannot delete or extend a successor's lock.
Only the holder ticks. The lock is released between ticks (not held across the
sleep), so another instance can take over immediately; the deterministic run id
keeps a handover safe even if both momentarily believe they are leader.

## Layout

| File | Responsibility |
|---|---|
| `app/modules/task_orchestration/schedule.py` | Pure cron/interval parsing and next-fire. No I/O. |
| `app/modules/task_orchestration/scheduler.py` | The tick: plan (pure) then persist-then-publish. |
| `app/modules/task_orchestration/transport.py` | Redis Streams `XADD` and the leader lock. |
| `app/modules/task_orchestration/service.py` | The leader-gated polling loop. |
| `app/scheduler/__main__.py` | Standalone process entry point. |

## Tests

Unit — no engine, no Redis, no network:

```bash
cd backend
uv run pytest tests/unit/test_task_schedule.py \
              tests/unit/test_task_scheduler_tick.py \
              tests/unit/test_task_scheduler_leader_lock.py
```

Integration — real StarRocks + Redis, skips cleanly without them:

```bash
uv run pytest tests/integration/test_task_scheduler.py -v
```

Override the targets with `NOVA_ORCH_SR_PORT`, `NOVA_ORCH_SR_HOST`,
`NOVA_ORCH_SR_USER`, `NOVA_ORCH_SR_PASSWORD` and `NOVA_ORCH_REDIS_URL`.
