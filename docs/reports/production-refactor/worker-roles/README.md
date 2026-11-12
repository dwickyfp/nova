# Dedicated worker lifecycles

Source parent `97012355`; exact tested application tree is recorded in
[commands.json](commands.json). This checkpoint separates workload execution;
it does not complete the production refactor or PG cutover.

- Default startup launches task, ingestion, ML, notebook, automation and
  autopilot in separate processes. A child exit fails the supervisor and stops
  its siblings. Production-style service commands select exactly one role.
- Each role has its own dependency lifecycle and heartbeat registry. Only
  task consumers count toward the existing task-worker health indicator.
  The child drain has a 30-second deadline, followed by cancellation; the
  launcher terminates remaining process groups after 40 seconds. Compose gives
  each worker 45 seconds before container termination.
- Existing development Compose now defines six worker containers, with CPU,
  memory and PID bounds. Prometheus discovery and acceptance checks cover every
  role; one healthy task worker cannot hide a missing ML worker.
- The integration migration subprocess selects ingestion only. Existing
  StarRocks control metadata, domain execution owners and queues are preserved.

Local verification: 10,895 unit/coverage passes (zero skips), 358 agent evals,
54 scorecard scenarios / 189 checks, mypy, Ruff on 125 changed Python files,
55 lifecycle/health focused tests, 12 monitoring checks, the user-data boundary
check, shell syntax and both affected Compose configuration checks pass.
The first full unit attempt was interrupted after eight sandbox-denied socket
failures; it is retained in the result record, followed by the complete passing
run with localhost permission.

[Local evidence](local-liveness.json) records six healthy processes and exactly
one fresh registry instance per role against the existing stack. No backing
containers or development data were reset. The first reload helper waited only
30 seconds, shorter than the documented supervisor deadline, so it refused to
start a replacement. After the old supervisor and all children exited, the
replacement started and all six checks passed. The helper now waits 50 seconds.
A scan of the local log found none of 16 configured secret values.

The old general worker was replaced in the active checkout. These are host
process results; separate-container image startup, per-domain readiness,
horizontal claim safety, durable PG jobs/receipts, isolated ML execution,
interactive agent/maintenance/outbox processes and the single production Compose
still require implementation or acceptance. No destructive failure injection or
capacity claim is included. The whole-feature matrix remains unchanged.

The preceding remote checkpoint passed all eight CI jobs, including 489 PG and
217 engine tests with 24 engine skips retained:
[CI evidence](../ci-2b441d80.json). This checkpoint still requires its own CI run.
