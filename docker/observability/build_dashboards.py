"""Generate the provisioned Nova Grafana dashboards from one panel catalog.

Run ``python docker/observability/build_dashboards.py`` after changing a panel.
The generated JSON files are committed so Grafana needs no build step.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent / "grafana" / "dashboards"
SOURCE = {"type": "prometheus", "uid": "nova-prometheus"}


def target(expression: str, legend: str = "") -> dict[str, Any]:
    return {"expr": expression, "legendFormat": legend, "refId": "A"}


def panel(
    kind: str,
    title: str,
    expression: str,
    *,
    legend: str = "",
    unit: str = "none",
    description: str = "",
    w: int = 12,
    h: int = 8,
    min_value: float | None = None,
    max_value: float | None = None,
    warning: float | None = None,
    critical: float | None = None,
    status: bool = False,
) -> dict[str, Any]:
    defaults: dict[str, Any] = {"unit": unit, "color": {"mode": "palette-classic"}}
    if min_value is not None:
        defaults["min"] = min_value
    if max_value is not None:
        defaults["max"] = max_value
    if warning is not None or critical is not None:
        steps = [{"color": "green", "value": None}]
        if warning is not None:
            steps.append({"color": "yellow", "value": warning})
        if critical is not None:
            steps.append({"color": "red", "value": critical})
        defaults["thresholds"] = {"mode": "absolute", "steps": steps}
    if status:
        defaults["mappings"] = [
            {"type": "value", "options": {
                "0": {"text": "Down", "color": "red"},
                "1": {"text": "Up", "color": "green"},
            }}
        ]
        defaults["noValue"] = "Not configured"
        defaults["color"] = {"mode": "thresholds"}
        defaults["thresholds"] = {"mode": "absolute", "steps": [
            {"color": "red", "value": None},
            {"color": "green", "value": 1},
        ]}
    result: dict[str, Any] = {
        "type": kind,
        "title": title,
        "description": description,
        "datasource": SOURCE,
        "targets": [target(expression, legend)],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "gridPos": {"h": h, "w": w, "x": 0, "y": 0},
        "transparent": False,
    }
    if kind == "stat":
        result["options"] = {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "orientation": "auto",
            "textMode": "auto",
            "colorMode": "background" if status else "value",
            "graphMode": "area" if not status else "none",
            "justifyMode": "auto",
        }
    elif kind == "timeseries":
        result["options"] = {
            "legend": {"displayMode": "table", "placement": "bottom", "calcs": ["lastNotNull", "max"]},
            "tooltip": {"mode": "multi", "sort": "desc"},
        }
        defaults["custom"] = {
            "drawStyle": "line", "lineInterpolation": "smooth", "lineWidth": 2,
            "fillOpacity": 12, "showPoints": "never", "spanNulls": False,
            "axisPlacement": "auto", "axisLabel": "", "axisColorMode": "text",
            "scaleDistribution": {"type": "linear"},
        }
    return result


def stat(title: str, expression: str, **kwargs: Any) -> dict[str, Any]:
    return panel("stat", title, expression, w=kwargs.pop("w", 4), h=kwargs.pop("h", 4), **kwargs)


def line(title: str, expression: str, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("legend", title)
    return panel("timeseries", title, expression, **kwargs)


def line_multi(title: str, series: list[tuple[str, str]], **kwargs: Any) -> dict[str, Any]:
    result = line(title, series[0][0], **kwargs)
    result["targets"] = [
        {"expr": expression, "legendFormat": label, "refId": chr(65 + index)}
        for index, (expression, label) in enumerate(series)
    ]
    return result


def health(title: str, job: str, *, w: int = 4) -> dict[str, Any]:
    return stat(title, f'max(up{{job="{job}"}})', status=True, w=w,
                description="Prometheus scrape state. No data means this target is not configured.")


def leader_state() -> dict[str, Any]:
    result = stat("Leader active", 'max(nova_scheduler_leader{job="nova-scheduler"})', status=True, w=6,
                  description="1 means a scheduler owns the active leader lease.")
    mapping = result["fieldConfig"]["defaults"]["mappings"][0]["options"]
    mapping["0"]["text"] = "No leader"
    mapping["1"]["text"] = "Leader"
    return result


def proxy_config_state() -> dict[str, Any]:
    result = stat("Proxy configured", 'max(nova_proxy_expected{job=~"nova-backend|nova-proxy"})', w=6,
                  description="Shows whether a MySQL listener is expected in this deployment.")
    result["fieldConfig"]["defaults"]["mappings"] = [
        {"type": "value", "options": {
            "0": {"text": "Disabled", "color": "blue"},
            "1": {"text": "Enabled", "color": "green"},
        }}
    ]
    return result


def row(title: str) -> dict[str, Any]:
    return {"type": "row", "title": title, "collapsed": False,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0}, "panels": []}


def text_panel(markdown: str) -> dict[str, Any]:
    return {"type": "text", "title": "", "options": {"mode": "markdown", "content": markdown},
            "gridPos": {"h": 3, "w": 24, "x": 0, "y": 0}, "transparent": True}


NAV = [
    ("Overview", "nova-overview", "nova-7c-operations-overview"),
    ("API & SQL", "nova-api-sql", "nova-7c-api-and-sql"),
    ("Jobs & agents", "nova-jobs-agents", "nova-7c-jobs-and-agents"),
    ("StarRocks", "nova-starrocks", "nova-7c-starrocks-engine"),
    ("Host system", "nova-host-system", "nova-7c-host-system"),
    ("Dependencies", "nova-dependencies", "nova-7c-dependencies"),
]

LINUX_FS_TYPES = "btrfs|ext4|xfs|zfs|f2fs"
ROOT_FS_USED = (
    '100 * (1 - node_filesystem_avail_bytes{job="node-exporter",mountpoint="/",fstype=~"' + LINUX_FS_TYPES + '"}'
    ' / node_filesystem_size_bytes{job="node-exporter",mountpoint="/",fstype=~"' + LINUX_FS_TYPES + '"})'
)
DOCKER_FS_USED = (
    '100 * (1 - node_filesystem_avail_bytes{job="node-exporter",mountpoint="/var/lib/docker",fstype=~"' + LINUX_FS_TYPES + '"}'
    ' / node_filesystem_size_bytes{job="node-exporter",mountpoint="/var/lib/docker",fstype=~"' + LINUX_FS_TYPES + '"})'
)
PRIMARY_FS_USED = f"({ROOT_FS_USED}) or on(instance) ({DOCKER_FS_USED})"


def dashboard(uid: str, title: str, description: str, panels: list[dict[str, Any]]) -> dict[str, Any]:
    # Place panels as rows of 24 grid units. Row headings always start a new line.
    x = y = row_height = 0
    for identifier, item in enumerate(panels, 1):
        width, height = item["gridPos"]["w"], item["gridPos"]["h"]
        if item["type"] == "row" or x + width > 24:
            y += row_height
            x = row_height = 0
        item["id"] = identifier
        item["gridPos"].update({"x": x, "y": y})
        x += width
        row_height = max(row_height, height)
        if item["type"] == "row":
            y += height
            x = row_height = 0
    return {
        "id": None, "uid": uid, "title": title, "description": description,
        "tags": ["nova", "operations"], "timezone": "browser", "schemaVersion": 41,
        "version": 1, "editable": False, "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {"refresh_intervals": ["15s", "30s", "1m", "5m", "15m"]},
        "links": [{"type": "link", "title": name, "url": f"/d/{target_uid}/{slug}",
                   "includeVars": False, "keepTime": True}
                  for name, target_uid, slug in NAV if target_uid != uid],
        "panels": panels,
    }


overview = dashboard("nova-overview", "Nova | Operations overview",
    "Service availability and the signals that need immediate attention.", [
    text_panel("**Nova operations**  ·  Check availability first, then latency, errors, and queue pressure. Use the links above to investigate a service."),
    row("Availability"),
    health("API", "nova-backend", w=6),
    stat("SQL proxy", 'max(nova_proxy_listener_up{job=~"nova-backend|nova-proxy"})', status=True, w=6,
         description="MySQL listener state. Check proxy configuration when intentionally disabled."),
    health("Scheduler", "nova-scheduler", w=6),
    health("Task worker", "nova-worker", w=6),
    health("Agent worker", "nova-agent-worker", w=6),
    health("StarRocks FE", "starrocks-fe", w=6),
    health("StarRocks BE", "starrocks-be", w=6),
    health("Linux runtime", "node-exporter", w=6),
    row("Traffic and latency"),
    line("API requests by status", 'sum by(status) (rate(nova_http_requests_total{job="nova-backend"}[5m]))', legend="HTTP {{status}}", unit="reqps",
         description="Completed HTTP requests per second, split by response code."),
    line("API latency, p95", 'histogram_quantile(0.95, sum by(le) (rate(nova_http_request_duration_seconds_bucket{job="nova-backend"}[5m])))', unit="s",
         description="95th percentile request duration over five minutes."),
    line("SQL queries by source", 'sum by(source) (rate(nova_sql_queries_total{job=~"nova-backend|nova-proxy"}[5m]))', legend="{{source}}", unit="reqps"),
    line("SQL latency, p95", 'histogram_quantile(0.95, sum by(le) (rate(nova_sql_query_duration_seconds_bucket{job=~"nova-backend|nova-proxy"}[5m])))', unit="s"),
    row("Execution pressure"),
    stat("Pending + lag", 'max(nova_worker_queue_depth{job="nova-worker"})', unit="short", warning=25, critical=100,
         description="Redis stream lag plus pending deliveries. Each worker reports the same group depth."),
    stat("Active task jobs", 'sum(nova_worker_active{job="nova-worker"})', unit="short"),
    stat("Active agent runs", 'sum(nova_agent_worker_active{job="nova-agent-worker"})', unit="short"),
    stat("SQL proxy sessions", 'sum(nova_proxy_connections_active{job=~"nova-backend|nova-proxy"})', unit="short"),
    stat("FE query queue", "sum(starrocks_fe_query_queue_pending)", unit="short", warning=10, critical=50),
    stat("Firing alerts", 'sum(ALERTS{alertstate="firing"}) or vector(0)', unit="short", warning=1, critical=3),
    line("Task results", 'sum by(status) (rate(nova_worker_jobs_total{job="nova-worker"}[5m]))', legend="{{status}}", unit="reqps"),
    line("Agent run results", 'sum by(status) (rate(nova_agent_worker_runs_total{job="nova-agent-worker"}[5m]))', legend="{{status}}", unit="reqps"),
    line("Service loop age", 'time() - nova_service_last_heartbeat_timestamp_seconds{job=~"nova-scheduler|nova-worker|nova-agent-worker"}',
         legend="{{job}}", unit="s", description="Time since the latest process heartbeat; worker heartbeats continue during long jobs."),
    line("Collector health", 'up{job=~"nova-backend|nova-scheduler|nova-worker|nova-agent-worker|starrocks-fe|starrocks-be|redis"}',
         legend="{{job}}", unit="short"),
    row("Dependencies"),
    stat("Cache and queue", 'max(redis_up{job="redis"})', status=True, w=6),
    stat("Stage storage", 'max(probe_success{service="stage-storage"})', status=True, w=6),
    stat("Policy service", 'max(probe_success{service="policy-service"})', status=True, w=6),
    stat("Metrics server", 'max(up{job="prometheus"})', status=True, w=6),
])

api_sql = dashboard("nova-api-sql", "Nova | API and SQL",
    "HTTP, SQL pipeline, and MySQL proxy behavior.", [
    text_panel("**API and SQL**  ·  Start with error rate and p95 latency. Use route and status breakdowns to identify the affected path."),
    row("Service and volume"),
    health("API scrape", "nova-backend", w=6),
    stat("Proxy listener", 'max(nova_proxy_listener_up{job=~"nova-backend|nova-proxy"})', status=True, w=6,
         description="Listener readiness, independent of the HTTP API scrape. Check proxy configuration when intentionally disabled."),
    stat("HTTP requests/s", 'sum(rate(nova_http_requests_total{job="nova-backend"}[5m]))', unit="reqps", w=6),
    stat("HTTP 5xx rate", '(sum(rate(nova_http_requests_total{job="nova-backend",status=~"5.."}[5m])) or vector(0)) / clamp_min(sum(rate(nova_http_requests_total{job="nova-backend"}[5m])), 0.001)',
         unit="percentunit", min_value=0, max_value=1, warning=0.01, critical=0.05, w=6,
         description="Share of completed requests returning a server error."),
    proxy_config_state(),
    stat("Proxy sessions", 'sum(nova_proxy_connections_active{job=~"nova-backend|nova-proxy"})', unit="short", w=6),
    stat("In-flight HTTP", 'sum(nova_http_requests_in_flight{job="nova-backend"})', unit="short", w=6),
    stat("Refused proxy sessions", 'sum(increase(nova_proxy_connections_rejected_total{job=~"nova-backend|nova-proxy"}[1h]))', unit="short", w=6,
         description="Connections rejected by the proxy capacity limit in the last hour."),
    line("Requests by route", 'sum by(route) (rate(nova_http_requests_total{job="nova-backend"}[5m]))', legend="{{route}}", unit="reqps"),
    line("Responses by status", 'sum by(status) (rate(nova_http_requests_total{job="nova-backend"}[5m]))', legend="{{status}}", unit="reqps"),
    row("Response time"),
    line("HTTP latency, p50", 'histogram_quantile(0.50, sum by(le) (rate(nova_http_request_duration_seconds_bucket{job="nova-backend"}[5m])))', unit="s"),
    line("HTTP latency, p95", 'histogram_quantile(0.95, sum by(le) (rate(nova_http_request_duration_seconds_bucket{job="nova-backend"}[5m])))', unit="s"),
    line("HTTP latency, p99 by route", 'histogram_quantile(0.99, sum by(le, route) (rate(nova_http_request_duration_seconds_bucket{job="nova-backend"}[5m])))', legend="{{route}}", unit="s"),
    line("SQL latency, p95 by source", 'histogram_quantile(0.95, sum by(le, source) (rate(nova_sql_query_duration_seconds_bucket{job=~"nova-backend|nova-proxy"}[5m])))', legend="{{source}}", unit="s"),
    row("SQL and proxy"),
    line("SQL results by source and status", 'sum by(source, status) (rate(nova_sql_queries_total{job=~"nova-backend|nova-proxy"}[5m]))', legend="{{source}} · {{status}}", unit="reqps"),
    line("Proxy results by status", 'sum by(status) (rate(nova_proxy_queries_total{job=~"nova-backend|nova-proxy"}[5m]))', legend="{{status}}", unit="reqps"),
    line("Open proxy sessions", 'sum(nova_proxy_connections_active{job=~"nova-backend|nova-proxy"})', unit="short"),
    line("FE connections", 'sum(starrocks_fe_connection_total{job="starrocks-fe"})', unit="short",
         description="StarRocks FE connections, including clients that bypass Nova."),
    row("API process"),
    line("CPU time", 'rate(process_cpu_seconds_total{job="nova-backend"}[5m])', legend="CPU cores", unit="short"),
    line("Resident memory", 'process_resident_memory_bytes{job="nova-backend"}', legend="Memory", unit="bytes"),
    line("Open file descriptors", 'process_open_fds{job="nova-backend"}', unit="short"),
    stat("Process start", 'process_start_time_seconds{job="nova-backend"}', unit="dateTimeAsIso", w=12),
    line("Proxy session rejections", 'sum(rate(nova_proxy_connections_rejected_total{job=~"nova-backend|nova-proxy"}[5m]))', unit="reqps", w=24),
])

jobs_agents = dashboard("nova-jobs-agents", "Nova | Jobs and agents",
    "Scheduler leadership, task worker throughput, and Studio Auto execution.", [
    text_panel("**Jobs and agents**  ·  Confirm the scheduler leader, then inspect queue depth, worker activity, run results, and duration."),
    row("Scheduler"),
    health("Scheduler scrape", "nova-scheduler", w=6),
    leader_state(),
    stat("Tick rate", 'sum(rate(nova_scheduler_ticks_total{job="nova-scheduler"}[5m]))', unit="reqps", w=6),
    stat("Pending + lag", 'max(nova_worker_queue_depth{job="nova-worker"})', unit="short", warning=25, critical=100, w=6,
         description="Stream lag plus pending deliveries, including work already in flight."),
    line("Scheduler ticks by result", 'sum by(status) (rate(nova_scheduler_ticks_total{job="nova-scheduler"}[5m]))', legend="{{status}}", unit="reqps"),
    line("Scheduler tick duration, p95", 'histogram_quantile(0.95, sum by(le) (rate(nova_scheduler_tick_duration_seconds_bucket{job="nova-scheduler"}[5m])))', unit="s"),
    row("Task worker"),
    health("Task worker scrape", "nova-worker", w=6),
    stat("Active jobs", 'sum(nova_worker_active{job="nova-worker"})', unit="short", w=6),
    stat("Completed/s", 'sum(rate(nova_worker_jobs_total{job="nova-worker",status=~"success|completed"}[5m]))', unit="reqps", w=6),
    stat("Failed/s", 'sum(rate(nova_worker_jobs_total{job="nova-worker",status=~"failed|error"}[5m]))', unit="reqps", warning=0.01, critical=0.1, w=6),
    line("Task results", 'sum by(status) (rate(nova_worker_jobs_total{job="nova-worker"}[5m]))', legend="{{status}}", unit="reqps"),
    line("Task duration, p95", 'histogram_quantile(0.95, sum by(le) (rate(nova_worker_job_duration_seconds_bucket{job="nova-worker"}[5m])))', unit="s"),
    line("Pending + lag", 'max(nova_worker_queue_depth{job="nova-worker"})', unit="short",
         description="Redis consumer-group pending deliveries plus undelivered lag; reported by each task worker, so use the maximum."),
    line("Task worker memory", 'process_resident_memory_bytes{job="nova-worker"}', legend="{{instance}}", unit="bytes"),
    row("Studio Auto worker"),
    health("Agent worker scrape", "nova-agent-worker", w=6),
    stat("Active runs", 'sum(nova_agent_worker_active{job="nova-agent-worker"})', unit="short", w=6),
    stat("Completed/s", 'sum(rate(nova_agent_worker_runs_total{job="nova-agent-worker",status="completed"}[5m]))', unit="reqps", w=6),
    stat("Failed/s", 'sum(rate(nova_agent_worker_runs_total{job="nova-agent-worker",status=~"failed|interrupted"}[5m]))', unit="reqps", warning=0.01, critical=0.1, w=6),
    line("Agent run outcomes", 'sum by(status) (rate(nova_agent_worker_runs_total{job="nova-agent-worker"}[5m]))', legend="{{status}}", unit="reqps"),
    line("Agent run duration", 'histogram_quantile(0.95, sum by(le) (rate(nova_agent_worker_run_duration_seconds_bucket{job="nova-agent-worker"}[5m])))', unit="s"),
    line("Agent poll and claim errors", 'sum by(phase) (rate(nova_agent_worker_poll_errors_total{job="nova-agent-worker"}[5m]))', legend="{{phase}}", unit="reqps",
         description="Errors while polling for work or claiming a queued run."),
    line("Agent worker CPU", 'rate(process_cpu_seconds_total{job="nova-agent-worker"}[5m])', unit="short"),
    line("Agent worker memory", 'process_resident_memory_bytes{job="nova-agent-worker"}', unit="bytes"),
    row("Migration jobs"),
    stat("Active migrations", 'sum(nova_migration_worker_active_jobs{job="nova-worker"})', unit="short", w=6,
         description="Migration jobs currently executing inside the task worker process."),
    stat("Terminal jobs/s", 'sum(rate(nova_migration_worker_jobs_total{job="nova-worker",status!="unfinished"}[5m]))', unit="reqps", w=6,
         description="Completed attempts that persisted a terminal outcome."),
    stat("Non-success/s", 'sum(rate(nova_migration_worker_jobs_total{job="nova-worker",status=~"partial|failed|interrupted"}[5m]))',
         unit="reqps", warning=0.01, critical=0.1, w=6,
         description="Partial, failed, or interrupted terminal outcomes; unfinished attempts are shown separately."),
    stat("Unfinished/s", 'sum(rate(nova_migration_worker_jobs_total{job="nova-worker",status="unfinished"}[5m]))',
         unit="reqps", warning=0.01, critical=0.1, w=6,
         description="An attempt that could not persist a terminal result; inspect worker logs and job state."),
    line("Migration outcomes", 'sum by(status) (rate(nova_migration_worker_jobs_total{job="nova-worker",status!="unfinished"}[5m]))',
         legend="{{status}}", unit="reqps"),
    line("Migration operations", 'sum by(operation) (rate(nova_migration_worker_jobs_total{job="nova-worker",status!="unfinished"}[5m]))',
         legend="{{operation}}", unit="reqps"),
    line("Migration duration, p95", 'histogram_quantile(0.95, sum by(le, operation) (rate(nova_migration_worker_job_duration_seconds_bucket{job="nova-worker",status!="unfinished"}[5m])))',
         legend="{{operation}}", unit="s"),
    line("Migration poll errors", 'sum by(phase) (rate(nova_migration_worker_poll_errors_total{job="nova-worker"}[5m]))',
         legend="{{phase}}", unit="reqps",
         description="Polling, claiming, or reconciling a migration job failed."),
    row("Search indexing"),
    stat("Active builds", 'sum(nova_search_builds_active{job="nova-backend"})', unit="short", w=8,
         description="Search index builds currently executing in the API process."),
    stat("Last successful poll age", 'time() - (nova_search_last_successful_poll_timestamp_seconds{job="nova-backend"} > 0)',
         unit="s", warning=60, critical=120, w=8,
         description="No data means the backend has not completed a successful search poll, or its scrape is unavailable."),
    stat("Build errors, 1h", 'sum(increase(nova_search_build_errors_total{job="nova-backend"}[1h]))',
         unit="short", warning=1, critical=4, w=8),
    stat("Poll errors, 1h", 'sum(increase(nova_search_poll_errors_total{job="nova-backend"}[1h]))',
         unit="short", warning=1, critical=4, w=12),
    stat("Reconcile errors, 1h", 'sum(increase(nova_search_reconciliation_errors_total{job="nova-backend"}[1h]))',
         unit="short", warning=1, critical=4, w=12,
         description="A top-level reconciliation error can also be counted as a poll error; do not add the two counts."),
    line("Build outcomes", 'sum by(status) (rate(nova_search_builds_total{job="nova-backend"}[5m]))',
         legend="{{status}}", unit="reqps"),
    line("Build duration, p95", 'histogram_quantile(0.95, sum by(le) (rate(nova_search_build_duration_seconds_bucket{job="nova-backend",status=~"success|failed"}[5m])))',
         unit="s", description="95th percentile duration of completed build attempts."),
    line_multi("Indexing loop errors", [
         ('rate(nova_search_poll_errors_total{job="nova-backend"}[5m])', "Poll failures"),
         ('rate(nova_search_reconciliation_errors_total{job="nova-backend"}[5m])', "Reconciliation failures"),
    ], unit="reqps", w=24,
         description="Repeated failures can leave builds pending or keep an active index stale."),
    row("Progress and recovery"),
    line("Graphs found due", 'sum(rate(nova_scheduler_due_graphs_total{job="nova-scheduler"}[5m]))', unit="reqps"),
    line("Graphs enqueued", 'sum(rate(nova_scheduler_enqueued_graphs_total{job="nova-scheduler"}[5m]))', unit="reqps"),
    line("Enqueue failures", 'sum(rate(nova_scheduler_enqueue_failures_total{job="nova-scheduler"}[5m]))', unit="reqps"),
    line("Invalid schedules", 'sum(rate(nova_scheduler_invalid_schedules_total{job="nova-scheduler"}[5m]))', unit="reqps"),
    line("Overlap skips", 'sum(rate(nova_scheduler_overlap_skips_total{job="nova-scheduler"}[5m]))', unit="reqps"),
    line("Worker reconciliation", 'sum by(status) (rate(nova_worker_reconciliations_total{job="nova-worker"}[5m]))', legend="{{status}}", unit="reqps"),
    line("Loop age by process", 'time() - nova_service_last_heartbeat_timestamp_seconds{job=~"nova-scheduler|nova-worker|nova-agent-worker"}',
         legend="{{job}}", unit="s", w=24,
         description="A rising line with a healthy scrape indicates a stalled process loop."),
])

starrocks = dashboard("nova-starrocks", "Nova | StarRocks engine",
    "Frontend query service and backend storage and compute health.", [
    text_panel("**StarRocks engine**  ·  Track query demand, queueing, memory, disk capacity, and compaction together."),
    row("Cluster health"),
    health("Frontend", "starrocks-fe", w=8), health("Backend", "starrocks-be", w=8),
    stat("Queries/s", 'sum(rate(starrocks_fe_query_total{job="starrocks-fe"}[5m]))', unit="reqps", w=8),
    stat("Pending queries", 'sum(starrocks_fe_query_queue_pending{job="starrocks-fe"})', unit="short", warning=10, critical=50, w=8),
    stat("FE connections", 'sum(starrocks_fe_connection_total{job="starrocks-fe"})', unit="short", w=8),
    stat("Compaction score", 'max(starrocks_fe_max_tablet_compaction_score{job="starrocks-fe"})', unit="short", warning=50, critical=100, w=8),
    row("Queries and admission"),
    line("Query throughput", 'sum(rate(starrocks_fe_query_total{job="starrocks-fe"}[5m]))', unit="reqps"),
    line("Query latency, p95", 'starrocks_fe_query_latency_ms{job="starrocks-fe",quantile="0.95"} / 1000', unit="s",
         description="StarRocks FE reports query latency as a percentile summary in milliseconds."),
    line("Query errors", 'sum(starrocks_fe_query_err_rate{job="starrocks-fe"})', unit="reqps"),
    line("Queries waiting", 'sum(starrocks_fe_query_queue_pending{job="starrocks-fe"})', unit="short"),
    row("Compute and memory"),
    line("BE CPU utilization", '100 * (1 - sum by(instance) (rate(starrocks_be_cpu{job="starrocks-be",mode="idle"}[5m])) / sum by(instance) (rate(starrocks_be_cpu{job="starrocks-be"}[5m])))', legend="{{instance}}", unit="percent", min_value=0, max_value=100),
    line("BE resident memory", 'process_memory_resident{job="starrocks-be"}', legend="{{instance}}", unit="bytes",
         description="Resident memory of the StarRocks backend process."),
    line("FE heap usage", '100 * sum(jvm_heap_size_bytes{job="starrocks-fe",type="used"}) / sum(jvm_heap_size_bytes{job="starrocks-fe",type="max"})', unit="percent", min_value=0, max_value=100),
    line("BE memory pool total", 'starrocks_be_memory_pool_bytes_total{job="starrocks-be"}', legend="{{instance}}", unit="bytes",
         description="Reported backend memory pool allocation; can remain zero when idle."),
    line("FE metadata log backlog", 'starrocks_fe_meta_log_count{job="starrocks-fe"}', unit="short", w=24),
    row("Storage and maintenance"),
    line("BE disk capacity used", '100 * (1 - starrocks_be_disks_avail_capacity{job="starrocks-be"} / starrocks_be_disks_total_capacity{job="starrocks-be"})', legend="{{path}}", unit="percent", min_value=0, max_value=100),
    line("Compaction score", 'starrocks_fe_max_tablet_compaction_score{job="starrocks-fe"}', unit="short"),
    line("Compaction failures", 'sum by(type) (rate(starrocks_be_engine_requests_total{job="starrocks-be",status="failed",type=~"base_compaction|cumulative_compaction"}[5m]))', legend="{{type}}", unit="reqps"),
    line("Backend traffic read", 'sum(rate(starrocks_be_bytes_read_total{job="starrocks-be"}[5m]))', unit="Bps"),
])

host_system = dashboard("nova-host-system", "Nova | Host system",
    "CPU, memory, filesystems, disk I/O, and networking of the Docker Linux runtime.", [
    text_panel("**Docker Linux runtime**  ·  These panels measure the Linux system running Docker. On macOS with OrbStack or Docker Desktop, they describe the Linux VM and its namespaces, not the macOS kernel or total Mac resource use."),
    row("Runtime summary"),
    health("Exporter scrape", "node-exporter", w=8),
    stat("CPU busy", '100 * (1 - avg(rate(node_cpu_seconds_total{job="node-exporter",mode="idle"}[5m])))',
         unit="percent", min_value=0, max_value=100, warning=75, critical=90, w=8,
         description="Five-minute share of Linux runtime CPU time spent outside idle."),
    stat("Memory used", '100 * (1 - node_memory_MemAvailable_bytes{job="node-exporter"} / node_memory_MemTotal_bytes{job="node-exporter"})',
         unit="percent", min_value=0, max_value=100, warning=75, critical=90, w=8,
         description="Uses MemAvailable so reclaimable cache is not counted as pressure."),
    stat("Runtime filesystem", f'max({PRIMARY_FS_USED})',
         unit="percent", min_value=0, max_value=100, warning=75, critical=90, w=8,
         description="Linux root filesystem when available, otherwise Docker data at /var/lib/docker (OrbStack)."),
    stat("Load, 1 minute", 'node_load1{job="node-exporter"}', unit="short", w=8,
         description="Linux load average is runnable or uninterruptible tasks, not a percentage."),
    stat("CPU cores", 'count(count by(cpu) (node_cpu_seconds_total{job="node-exporter",mode="idle"}))', unit="short", w=8),
    row("CPU and load"),
    line("CPU busy over time", '100 * (1 - avg(rate(node_cpu_seconds_total{job="node-exporter",mode="idle"}[5m])))',
         unit="percent", min_value=0, max_value=100),
    line("CPU busy by core", '100 * (1 - rate(node_cpu_seconds_total{job="node-exporter",mode="idle"}[5m]))',
         legend="Core {{cpu}}", unit="percent", min_value=0, max_value=100),
    line_multi("Load average", [
         ('node_load1{job="node-exporter"}', "1 minute"),
         ('node_load5{job="node-exporter"}', "5 minutes"),
         ('node_load15{job="node-exporter"}', "15 minutes"),
    ], unit="short",
         description="Compare load with the CPU core count above."),
    line("CPU time by mode", '100 * avg by(mode) (rate(node_cpu_seconds_total{job="node-exporter"}[5m]))',
         legend="{{mode}}", unit="percent", min_value=0, max_value=100),
    row("Memory and swap"),
    line("Memory used", 'node_memory_MemTotal_bytes{job="node-exporter"} - node_memory_MemAvailable_bytes{job="node-exporter"}',
         unit="bytes", description="Used memory excludes reclaimable page cache."),
    line("Available memory", 'node_memory_MemAvailable_bytes{job="node-exporter"}', unit="bytes"),
    line("Swap used", 'node_memory_SwapTotal_bytes{job="node-exporter"} - node_memory_SwapFree_bytes{job="node-exporter"}',
         unit="bytes", description="Zero can mean the Linux runtime has no swap configured."),
    line_multi("Swap activity", [
         ('rate(node_vmstat_pswpin{job="node-exporter"}[5m])', "Pages in/s"),
         ('rate(node_vmstat_pswpout{job="node-exporter"}[5m])', "Pages out/s"),
    ], unit="ops",
         description="Pages swapped in or out per second, when the VM reports these counters."),
    row("Filesystems"),
    line("Filesystem used", '100 * (1 - node_filesystem_avail_bytes{job="node-exporter",fstype=~"' + LINUX_FS_TYPES + '"} / node_filesystem_size_bytes{job="node-exporter",fstype=~"' + LINUX_FS_TYPES + '"})',
         legend="{{mountpoint}} · {{device}}", unit="percent", min_value=0, max_value=100,
         description="Linux persistent filesystems only. Virtual Mac file shares are excluded."),
    line("Filesystem available", 'node_filesystem_avail_bytes{job="node-exporter",fstype=~"' + LINUX_FS_TYPES + '"}',
         legend="{{mountpoint}} · {{device}}", unit="bytes"),
    line("Inodes used", '100 * (1 - node_filesystem_files_free{job="node-exporter",fstype=~"' + LINUX_FS_TYPES + '"} / node_filesystem_files{job="node-exporter",fstype=~"' + LINUX_FS_TYPES + '"})',
         legend="{{mountpoint}}", unit="percent", min_value=0, max_value=100),
    line("Filesystem read errors", 'node_filesystem_device_error{job="node-exporter",fstype=~"' + LINUX_FS_TYPES + '"}',
         legend="{{mountpoint}}", unit="short",
         description="1 means the exporter could not read a mount; inspect container mount permissions."),
    row("Disk I/O"),
    line("Disk reads", 'sum by(device) (rate(node_disk_read_bytes_total{job="node-exporter"}[5m]))',
         legend="{{device}}", unit="Bps"),
    line("Disk writes", 'sum by(device) (rate(node_disk_written_bytes_total{job="node-exporter"}[5m]))',
         legend="{{device}}", unit="Bps"),
    line("Disk operations", 'sum by(device) (rate(node_disk_reads_completed_total{job="node-exporter"}[5m]) + rate(node_disk_writes_completed_total{job="node-exporter"}[5m]))',
         legend="{{device}}", unit="ops"),
    line("Disk time busy", '100 * rate(node_disk_io_time_seconds_total{job="node-exporter"}[5m])',
         legend="{{device}}", unit="percent", min_value=0,
         description="Busy time per device; virtual disks can report this differently from physical disks."),
    row("Network"),
    line("Received traffic", 'sum by(device) (rate(node_network_receive_bytes_total{job="node-exporter",device!~"lo|veth.*|docker.*|br-.*"}[5m]))',
         legend="{{device}}", unit="Bps",
         description="Interfaces of the Docker Linux network namespace; not macOS Wi-Fi or Ethernet totals."),
    line("Transmitted traffic", 'sum by(device) (rate(node_network_transmit_bytes_total{job="node-exporter",device!~"lo|veth.*|docker.*|br-.*"}[5m]))',
         legend="{{device}}", unit="Bps"),
    line("Receive errors", 'sum by(device) (rate(node_network_receive_errs_total{job="node-exporter",device!~"lo|veth.*|docker.*|br-.*"}[5m]))',
         legend="{{device}}", unit="reqps"),
    line("Transmit errors", 'sum by(device) (rate(node_network_transmit_errs_total{job="node-exporter",device!~"lo|veth.*|docker.*|br-.*"}[5m]))',
         legend="{{device}}", unit="reqps"),
])

dependencies = dashboard("nova-dependencies", "Nova | Dependencies",
    "Cache and queue, stage storage, policy service, and metrics collection.", [
    text_panel("**Dependencies**  ·  A service can answer a scrape while its dependency is unavailable. Compare availability with application errors."),
    row("Availability"),
    stat("Cache and queue", 'max(redis_up{job="redis"})', status=True, w=6),
    stat("Stage storage", 'max(probe_success{service="stage-storage"})', status=True, w=6),
    stat("Policy service", 'max(probe_success{service="policy-service"})', status=True, w=6),
    health("Prometheus", "prometheus", w=6),
    row("Cache and queue"),
    line("Memory used", 'redis_memory_used_bytes{job="redis"}', unit="bytes"),
    line("Connected clients", 'redis_connected_clients{job="redis"}', unit="short"),
    line("Commands/s", 'rate(redis_commands_processed_total{job="redis"}[5m])', unit="reqps"),
    line("Cache hit ratio", '100 * rate(redis_keyspace_hits_total{job="redis"}[5m]) / clamp_min(rate(redis_keyspace_hits_total{job="redis"}[5m]) + rate(redis_keyspace_misses_total{job="redis"}[5m]), 0.001)', unit="percent", min_value=0, max_value=100),
    row("Endpoint checks"),
    line("Stage storage probe", 'probe_success{service="stage-storage"}', unit="short"),
    line("Policy service probe", 'probe_success{service="policy-service"}', unit="short"),
    line("Probe duration", 'probe_duration_seconds{job="dependency-probe"}', legend="{{service}}", unit="s"),
    line("Scrape failures", '1 - up{job=~"starrocks-fe|starrocks-be|nova-backend|nova-scheduler|nova-worker|nova-agent-worker|redis"}', legend="{{job}}", unit="short"),
    row("Monitoring system"),
    line("Prometheus samples ingested/s", 'rate(prometheus_tsdb_head_samples_appended_total[5m])', unit="reqps"),
    line("Prometheus series", 'prometheus_tsdb_head_series', unit="short"),
])


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for entry in (overview, api_sql, jobs_agents, starrocks, host_system, dependencies):
        path = ROOT / f"{entry['uid']}.json"
        path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{path.relative_to(ROOT.parent.parent.parent)}: {len(entry['panels'])} panels")


if __name__ == "__main__":
    main()
