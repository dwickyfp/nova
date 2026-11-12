# Runtime and scaling guide

Inherit the [root](../../../AGENTS.md) and [backend](../../AGENTS.md) contracts.
Also read this guide for `app/main.py`, `app/scheduler/`, proxy startup,
background controllers and process-role changes outside this directory.
Deployment wiring follows the [Docker guide](../../../docker/AGENTS.md).

## Current entrypoints and target boundaries

Read `__main__.py` for selected-role wiring, `supervisor.py` for the default
launcher, and `health.py` plus `app/process_health.py` for process liveness.
Read [entrypoints](../../../docs/architecture/production-refactor/ENTRYPOINTS.csv)
and [dependencies](../../../docs/architecture/production-refactor/DEPENDENCIES.md)
for the recorded ownership map; verify both against source before changing it.

The no-argument worker launcher supervises separate OS child processes. A
deployment worker selects one role. Do not recombine heavy workload loops in one
HTTP process or create a new scheduler/consumer framework for a feature.
Maintenance owns Search polling/builds, Catalog/News refresh and ML retention;
HTTP requests persist the corresponding intent rather than starting these loops.
Per-process telemetry flushing remains with the originating process until its
own approved durable handoff is implemented.

Interactive agent separation, durable outbox integration and full dependency
readiness are target work, not implied by existing worker-role names. Current
process-local consent/cancel ownership constrains agent routing. Preserve the
gateway's affinity until durable recovery and cross-replica ownership are tested.
An agent workload process runs the shared bounded engine; it does not restore
historical Smart/Auto multi-agent product behavior.

## Adding or changing a workload

Record the following in the owning architecture/entrypoint record and implement
the applicable contracts before declaring the role ready:

| Concern | Required decision and evidence |
| --- | --- |
| Ownership | One domain service and durable state owner; explicit producer and consumer paths |
| Identity | Caller or authorized service principal, active role, context version and workload-specific credentials |
| Admission | Bounded concurrency, queue/backlog limits, dependency pools and rejection/backpressure behavior |
| Claims | Durable claim, lease/fencing and cancellation ownership; duplicate submission and stale-worker refusal |
| Effects | Immutable definition/intent, receipts and verification of unknown outcomes |
| Resources | CPU, memory, PIDs, time/output limits and isolation of user code/model execution |
| Lifecycle | Initialization failure, task supervision, drain deadline and descendant cleanup |
| Health | Process liveness separately from dependency/schema/epoch/capacity readiness |
| Operations | Stable instance labels, metrics, redacted logs, alerts and recovery procedure |

- Extend the existing role registry, CLI validation, lazy initialization and
  supervision together. Update normal development startup and production
  service wiring so required roles run without an extra enable switch.
- Initialize only the selected role's dependencies and credentials. Keep PG
  migration credentials and the Docker socket out of application/worker roles.
  Existing pre-cutover schema bootstrap is not a precedent for migrating PG
  from a worker. Follow the [metadata guide](../../metadata_migrations/AGENTS.md).
- Supervise every long-lived task. Unexpected return, task failure or broken
  heartbeat must be observable and fail the owning process when appropriate;
  do not leave a healthy parent around a dead worker. Close partially initialized
  dependencies and reap child processes on failure and shutdown.
- Stop admission before draining. Bound waits, propagate cancellation and close
  pools in order. Keep container shutdown grace longer than the process's drain
  and termination budgets. Interrupted external effects retain recoverable state.
- Treat Redis as transport/cache in the target design. Existing Redis claims
  are not proof of migrated durable ownership. A worker death or lost wakeup
  must not erase committed work or cause an unverified mutation retry.

## Proving scale and recovery

Do not increase replicas until claims, fencing, actor/session isolation, resource
admission and side-effect recovery support multiple owners. Test concurrent
instances and stale workers on the same durable queue, not only independent
happy-path workers. A successful Compose configuration check proves syntax, not
startup, safe replication or capacity.

Run backend gates and affected process-role, worker supervision, heartbeat,
maintenance and domain tests. Verify role selection and default startup, partial
initialization cleanup, unexpected exit, cancellation, graceful/hard shutdown,
registry identity and health failure. Verify normal product submission reaches
the owning process without enabling a hidden path.

Use disposable infrastructure for worker/dependency death, Redis loss, unknown
external commits and other fault tests. Reuse the running local stack for safe
functional checks. Record the actual device, limits, concurrency, duration,
latency/backlog and failures. The owner's release-specific load waiver is not
evidence of capacity, and single-host tests cannot certify cross-host HA.
