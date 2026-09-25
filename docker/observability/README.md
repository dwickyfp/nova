# Nova observability

Grafana and Prometheus are provisioned from files in this directory. The six
dashboards open in Grafana's **Nova** folder:

| Dashboard | What to inspect |
| --- | --- |
| Operations overview | Availability, request latency, SQL traffic, execution pressure, dependencies |
| API and SQL | Route errors and latency, SQL pipeline, proxy listener and sessions, API process |
| Jobs and agents | Scheduler leadership, queue backlog, task and Studio Auto workers, migration jobs, search indexing, recovery |
| StarRocks engine | FE query traffic and queues, BE CPU, memory, disk, compaction |
| Host system | Docker Linux runtime CPU, memory, swap, filesystems, disk I/O, network, load |
| Dependencies | Cache and queue, stage storage, policy service, monitoring pipeline |

Each panel shows **No data** when its metric has not been emitted yet. An
absent availability target appears as **No data** or **Not configured**, never
as healthy. Prometheus alerts appear in
the overview's firing-alert count and in Prometheus's Alerts page; this stack
does not configure notification destinations.
Application panels and alerts select the process's scrape job so default zero
gauges from another process cannot hide a stopped scheduler or worker.

## Start with host application processes

The existing `dev.sh` runs the API, scheduler, task worker, and Studio Auto
worker on the host. The observability stack scrapes them via
`host.docker.internal` on ports 8000, 9101, 9102, and 9103. Docker Desktop or
OrbStack must make this hostname available inside containers.

1. Set `GRAFANA_ADMIN_PASSWORD` to a unique value in `docker/.env`. Keep the
   existing Nova and dependency settings there. Set `NOVA_METRICS_MODE=host`.
2. From `docker/`, start the engine and observability stack:

   ```bash
   docker compose -f docker-compose-engine.yml -f docker-compose.dev.yml \
     -f docker-compose-observability.yml up -d
   ```

3. From the repository root, start Nova's host processes with `./dev.sh`.

The metrics servers bind to `0.0.0.0` so Prometheus in Docker can reach them.
The API already binds there in `dev.sh`. Protect the development machine's
9101–9103 ports from untrusted networks. Grafana and Prometheus themselves are
published only on loopback.

The node exporter runs in the Docker Linux host network and process namespaces
and reads its root filesystem through a read-only mount. Prometheus scrapes it
at `host.docker.internal:9100` in both application modes. On a native Linux
Docker host this describes that Linux host. On macOS with OrbStack or Docker
Desktop it describes the Linux runtime and visible mounts, **not** macOS CPU,
RAM, physical disks, or Wi-Fi usage. Filesystem charts include Linux internal
filesystems such as btrfs, ext4, and xfs; Mac virtiofs shares are excluded.
The primary filesystem card and alert use `/` where exported, falling back to
`/var/lib/docker` on OrbStack. A missing mount appears as **No data**. Docker
Desktop's optional host-network feature must be enabled for this deployment.
Because node exporter uses host networking and listens on port 9100, restrict
that port to trusted networks using the host firewall in shared environments.

## Start with container application processes

Configure `WORKER_IMPERSONATION_USER`, `WORKER_IMPERSONATION_PASSWORD`, and
`WORKER_IMPERSONATION_ROLE` in `docker/.env` for the dedicated task worker
account. Set `NOVA_METRICS_MODE=container`. Then run from `docker/`:

```bash
docker compose --profile app -f docker-compose-engine.yml \
  -f docker-compose-observability.yml up -d
```

This profile starts the API and separate scheduler, task worker, and Studio
Auto worker containers. Prometheus scrapes their Docker service names. Do not
run `dev.sh` at the same time: both modes use the same engine and can create
duplicate workers.

Switching modes changes the mounted scrape-target directory. Recreate
Prometheus with the selected `NOVA_METRICS_MODE` value after switching modes:

```bash
docker compose -f docker-compose-engine.yml \
  -f docker-compose-observability.yml up -d --force-recreate prometheus
```

## Open and verify

- Grafana: [http://localhost:3001](http://localhost:3001) (set both
  `GRAFANA_PORT` and `GRAFANA_ROOT_URL` when changing its host port)
- Prometheus targets: [http://localhost:9090/targets](http://localhost:9090/targets)
- Prometheus alerts: [http://localhost:9090/alerts](http://localhost:9090/alerts)

Log in to Grafana as `GRAFANA_ADMIN_USER` (default `admin`) with the password
from `docker/.env`. In Prometheus Targets, confirm the FE, BE, Redis exporter,
node exporter, four Nova processes, and both dependency probes are healthy. The SQL proxy
listener is represented by `nova_proxy_listener_up` on the API target because
it shares that process in the default deployment. If the proxy runs as a
separate `app.proxy` process, configure `prometheus/targets/host/proxy.yml` for
port 9104 or `prometheus/targets/container/proxy.yml` for its container
address. Both files are empty by default, so an unused standalone proxy target
cannot create a false alarm.

Run the read-only acceptance checker after the stack is up. It reads Grafana
credentials from `docker/.env` and does not require a password on the command
line:

```bash
python docker/observability/check_stack.py --mode host
python docker/observability/check_stack.py --mode container
```

Run only the command matching the selected mode. The checker verifies scrape
targets, startup, a successful search indexing poll, and dependency metrics,
Linux runtime CPU, memory, filesystem,
disk, network, and load metrics, alert rules, and all six provisioned
dashboard URLs. It exits nonzero if a required check fails.

## Alert thresholds

| Signal | Rule |
| --- | --- |
| Required scrape unavailable | 2 minutes |
| MySQL listener down while configured | 1 minute |
| Scheduler or worker heartbeat stale | Older than 90 seconds for 2 minutes |
| No scheduler leader | 2 minutes |
| Cache, stage storage, or policy probe failed | 2 minutes |
| API 5xx rate | Above 5% for 5 minutes with at least 0.5 requests/s |
| Task queue pending + lag | Above 100 for 10 minutes |
| Task or agent failures | Above 10% for 5 minutes when work is flowing |
| Studio Auto poll or claim errors | More than 3 in 10 minutes for 2 minutes |
| Migration job partial, failed, interrupted, or unfinished | On the next evaluation |
| Migration worker poll, claim, or reconciliation errors | More than 3 in 10 minutes for 2 minutes |
| Search indexing poll or reconciliation errors | More than 3 in 10 minutes for 2 minutes |
| Search indexing poll stale or never successful | Last success older than 2 minutes for 2 minutes, after a 2-minute process startup grace |
| Search index build failure | One failed build in 10 minutes |
| Scheduler enqueue failure or partial reconciliation | On the next evaluation |
| StarRocks FE p95 query latency | Above 5 seconds for 10 minutes |
| StarRocks BE disk usage | Above 90% for 10 minutes |
| Docker Linux runtime CPU or memory usage | Above 90% for 10 minutes |
| Docker Linux persistent filesystem usage | Above 90% for 10 minutes |

Thresholds in the overview are visual guides; the evaluated rules are in
`prometheus/alerts.yml`. Configure a notification receiver separately if
alerts must be sent outside Prometheus and Grafana.

If a process is absent, inspect its target's scrape error first. If the target
is healthy but a dashboard panel is empty, inspect the metric name in
Prometheus Explore; some StarRocks metrics are emitted only after relevant
activity or only on the FE leader. A standalone worker's scrape can be healthy
while its loop is stalled, so compare the **Loop age by process** panel too.

The dashboard JSON files are generated by `build_dashboards.py`. After changing
the panel catalog, run:

```bash
python docker/observability/build_dashboards.py
```

Grafana reloads the committed dashboard files automatically. Prometheus
reloads file-based target changes automatically; a change to
`prometheus.yml` or `alerts.yml` requires a Prometheus reload or container
restart. Grafana's default plugin auto-update is disabled to avoid update
downloads at startup; the dashboards use only its Prometheus datasource.

## Metric boundaries

Metric labels contain bounded route templates, outcome, source, job, and
service names. They do not contain SQL text, user names, run IDs, storage
paths, or credentials. Detailed per-query and per-run records stay in Nova's
existing monitoring UI and `NOVA_SYSTEM`; Grafana shows operational aggregates.

The source of the StarRocks FE/BE panel names and alert thresholds is the
[official StarRocks monitoring documentation](https://docs.starrocks.io/docs/administration/management/monitoring/alert/).
The node exporter host namespace and read-only root mount follow its
[official container deployment guide](https://github.com/prometheus/node_exporter/blob/master/README.md#docker).
Grafana dashboard and data source files follow
[Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/).
