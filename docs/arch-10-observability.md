# Architecture 10: Nova Observability

> Prometheus metrics and provisioned Grafana dashboards for Nova's engine and runtime processes.

---

## Architecture

```text
StarRocks FE :8030/metrics ───┐
StarRocks BE :8040/metrics ───┤
Nova API :8000/metrics ───────┤
Scheduler :9101/metrics ──────┤
Task worker :9102/metrics ────┼──> Prometheus ──> Grafana / Nova dashboards
Agent worker :9103/metrics ───┤        │
Redis exporter :9121 ─────────┤        └────────> alert rules
Docker Linux runtime :9100 ──┤
Stage/policy HTTP probes ─────┘
```

Nova's existing admin-gated monitoring API continues to provide detailed
query, audit, task, and readiness records. Prometheus stores operational time
series; Grafana presents those series in six provisioned dashboards. The
observability stack never queries `NOVA_SYSTEM` directly and adds no persistent
application database.

## Implementation

`backend/app/observability/metrics.py` defines the shared low-cardinality
contract. FastAPI records request rate, latency, and in-flight work using route
templates. `QueryService.execute()` records every SQL statement that passes
through Nova's query pipeline, including direct calls from features and batch
calls from the editor or MySQL proxy. Sources are restricted to `web`,
`mysql_proxy`, and `internal`. The proxy records listener availability,
connections, and command results.

The embedded AI Search indexer reports successful polling age, poll and
reconciliation errors, active builds, bounded outcomes, and duration. It uses
no index names or user data as metric labels.

The standalone scheduler, task worker, and Studio Auto worker each expose a
small Prometheus HTTP endpoint. Scheduler metrics cover leadership, ticks,
due/published graphs, invalid schedules, overlap skips, and enqueue failures.
The task worker reports active graph attempts, outcomes, duration, Redis group
lag plus pending deliveries, and reconciliation results. Its migration job
runner reports active jobs, bounded operation/outcome, duration, and queue
polling errors. Studio Auto reports active runs, outcomes, duration, claim and
polling errors, and loop heartbeat. Process and Python runtime metrics are
emitted by `prometheus-client` on each endpoint.

The task worker heartbeat is updated by its consumer loop after successful
polling and while all execution slots are occupied. This distinguishes a
stalled consumer from its independent Redis liveness task without treating a
long valid job as stalled. Queue depth is sampled from the consumer group in
the main consume loop. Redis returns an unknown lag after some stream
mutations; Nova exports `NaN` then instead of a false zero. Grafana aggregates
the worker target's queue with `max` because each scaled worker observes the
same consumer-group backlog.

`docker/docker-compose-observability.yml` adds Prometheus, Grafana, Redis and
node exporters, and HTTP dependency probes. File service discovery selects
either host processes launched by `dev.sh` or container application processes
under the `app` profile. The default deployment keeps the MySQL proxy embedded in
the API; a separate proxy can export port 9104 with an explicit target file.
Node exporter observes the Linux runtime hosting Docker. Under OrbStack or
Docker Desktop, that is the Linux VM, not the macOS kernel or total Mac capacity.

## Integration Points

- **Operations overview:** current availability, request and SQL traffic,
  latency, task pressure, dependencies, and firing alerts.
- **API and SQL:** route errors, response percentiles, SQL pipeline outcomes,
  proxy listener/sessions, and API process resources.
- **Jobs and agents:** scheduler leadership and failures, queue depth, task
  graph and migration job outcomes, Studio Auto outcomes, durations, polling
  errors, AI Search indexing health, and process resources.
- **StarRocks engine:** native FE/BE query, admission, memory, disk, and
  compaction metrics. The queries are checked against local StarRocks 4.1.4
  `/metrics` output.
- **Host system:** Linux runtime CPU, load, memory, swap, internal filesystem
  capacity, disk I/O, and network. On OrbStack, filesystem capacity uses the
  Docker data mount when `/` is not exposed as an internal filesystem.
- **Dependencies:** Redis health and traffic, stage storage and Ranger policy
  HTTP probes, scrape failures, and Prometheus health.

Prometheus rules cover scrape loss, stalled loops, missing scheduler leader,
proxy listener failure, dependency loss, backlog, partial reconciliation,
enqueue failure, task/agent failure ratio, search indexing errors or stalled
polling, HTTP 5xx ratio, FE latency, BE disk use, and Linux runtime CPU,
memory, and filesystem pressure. Rules are visible in Prometheus and Grafana.
Notification delivery needs an operator-selected contact point; no destination
is stored here.

This design adapts the native FE/BE scrape, file provisioning, alerts, and
acceptance-check pattern from
[StarRocks Production Automation](https://github.com/abdull93/StarRocks-Production-automation/tree/0c27b7a640cf1f210282e0467444fe6af32de99f).
That repository's monitoring installer assumes fixed VM counts, dedicated
Ubuntu hosts, and a specific MinIO appliance. Nova instead uses its own Docker
topology and storage abstraction. Its source has no license file, so Nova's
implementation is independent rather than copied from that code.

## Configuration

Follow [the observability runbook](../docker/observability/README.md) for both
deployment modes and Grafana access. Metrics contain no SQL text, usernames,
task IDs, storage paths, or credentials. Grafana and Prometheus host ports bind
to loopback; application metrics ports bind to the host in development so
Prometheus can reach them through `host.docker.internal`. Restrict access to
those ports at the host firewall. Secrets remain in ignored `.env` files.

The dashboards show missing series as **No data**. A service with no scrape
target is not treated as healthy. FE journal replay lag is not presented as a
zero-value health signal on Nova's current single-FE topology. Ephemeral ML
training processes are observed through the API's process and SQL metrics,
while their detailed run records remain in Nova's ML surfaces. On a multi-host
production deployment, deploy one node exporter per Linux host and select a
contact point for alert delivery; the local Compose stack does not claim
Mac-wide host metrics or external notification delivery.
