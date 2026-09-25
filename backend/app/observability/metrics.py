"""Low-cardinality metrics shared by Nova's HTTP and standalone processes."""

from __future__ import annotations

import os
import time
from contextvars import ContextVar

from prometheus_client import Counter, Gauge, Histogram, start_http_server

SQL_SOURCE: ContextVar[str] = ContextVar("nova_sql_metric_source", default="internal")

HTTP_REQUESTS = Counter(
    "nova_http_requests_total",
    "Completed HTTP requests.",
    ("method", "route", "status"),
)
HTTP_REQUEST_DURATION = Histogram(
    "nova_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
HTTP_IN_FLIGHT = Gauge("nova_http_requests_in_flight", "HTTP requests currently executing.")

SQL_QUERIES = Counter(
    "nova_sql_queries_total",
    "SQL statements completed through Nova's query service.",
    ("source", "status"),
)
SQL_QUERY_DURATION = Histogram(
    "nova_sql_query_duration_seconds",
    "SQL statement duration in seconds, including Nova's dialect pipeline.",
    ("source", "status"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 120),
)

PROXY_CONNECTIONS_ACTIVE = Gauge(
    "nova_proxy_connections_active", "Current MySQL proxy client connections."
)
PROXY_CONNECTIONS_REJECTED = Counter(
    "nova_proxy_connections_rejected_total", "MySQL proxy connections refused at capacity."
)
PROXY_LISTENER_UP = Gauge("nova_proxy_listener_up", "Whether the MySQL proxy listener is bound.")
PROXY_EXPECTED = Gauge("nova_proxy_expected", "Whether this process is configured to serve MySQL.")
PROXY_QUERIES = Counter(
    "nova_proxy_queries_total", "MySQL COM_QUERY commands completed.", ("status",)
)

SCHEDULER_LEADER = Gauge("nova_scheduler_leader", "Whether this scheduler holds the leader lease.")
SCHEDULER_TICKS = Counter("nova_scheduler_ticks_total", "Scheduler ticks by outcome.", ("status",))
SCHEDULER_TICK_DURATION = Histogram(
    "nova_scheduler_tick_duration_seconds",
    "Duration of a scheduler tick in seconds.",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
SCHEDULER_DUE_GRAPHS = Counter(
    "nova_scheduler_due_graphs_total", "New due graph runs persisted by the scheduler."
)
SCHEDULER_ENQUEUED_GRAPHS = Counter(
    "nova_scheduler_enqueued_graphs_total", "New graph runs published to the worker stream."
)
SCHEDULER_ENQUEUE_FAILURES = Counter(
    "nova_scheduler_enqueue_failures_total", "Due graph runs that failed to persist or publish."
)
SCHEDULER_INVALID_SCHEDULES = Counter(
    "nova_scheduler_invalid_schedules_total",
    "Task schedules skipped because they could not be parsed.",
)
SCHEDULER_OVERLAP_SKIPS = Counter(
    "nova_scheduler_overlap_skips_total", "Due graph runs skipped by overlap policy."
)

WORKER_ACTIVE = Gauge("nova_worker_active", "Graph runs currently executing in this worker.")
WORKER_JOBS = Counter(
    "nova_worker_jobs_total", "Graph run handling attempts by outcome.", ("status",)
)
WORKER_JOB_DURATION = Histogram(
    "nova_worker_job_duration_seconds",
    "Graph run delivery handling duration in seconds.",
    ("status",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300, 1800),
)
WORKER_QUEUE_DEPTH = Gauge(
    "nova_worker_queue_depth", "Redis stream lag plus pending deliveries for the task worker group."
)
WORKER_RECONCILIATIONS = Counter(
    "nova_worker_reconciliations_total",
    "Task worker reconciliation passes by outcome.",
    ("status",),
)
WORKER_RECONCILIATION_DURATION = Histogram(
    "nova_worker_reconciliation_duration_seconds",
    "Task worker reconciliation pass duration in seconds.",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300),
)

MIGRATION_WORKER_ACTIVE_JOBS = Gauge(
    "nova_migration_worker_active_jobs", "Migration jobs currently executing in this worker."
)
MIGRATION_WORKER_JOBS = Counter(
    "nova_migration_worker_jobs_total",
    "Migration job handling attempts by operation and outcome. "
    "Unfinished means the terminal update did not return successfully.",
    ("operation", "status"),
)
MIGRATION_WORKER_JOB_DURATION = Histogram(
    "nova_migration_worker_job_duration_seconds",
    "Migration job handling duration in seconds, including cleanup.",
    ("operation", "status"),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300, 1800),
)
MIGRATION_WORKER_POLL_ERRORS = Counter(
    "nova_migration_worker_poll_errors_total",
    "Migration queue poll, claim, and reconciliation failures by bounded phase.",
    ("phase",),
)

AGENT_WORKER_ACTIVE = Gauge(
    "nova_agent_worker_active", "Studio Auto agent runs currently executing in this worker."
)
AGENT_WORKER_RUNS = Counter(
    "nova_agent_worker_runs_total", "Studio Auto agent runs by outcome.", ("status",)
)
AGENT_WORKER_RUN_DURATION = Histogram(
    "nova_agent_worker_run_duration_seconds",
    "Studio Auto agent run duration in seconds.",
    ("status",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300, 1800),
)
AGENT_WORKER_POLL_ERRORS = Counter(
    "nova_agent_worker_poll_errors_total",
    "Studio Auto poll and claim failures by phase.",
    ("phase",),
)

SEARCH_POLL_ERRORS = Counter(
    "nova_search_poll_errors_total", "AI Search build queue poll failures."
)
SEARCH_LAST_SUCCESSFUL_POLL = Gauge(
    "nova_search_last_successful_poll_timestamp_seconds",
    "Unix time of the last successful AI Search build queue poll.",
)
SEARCH_RECONCILIATION_ERRORS = Counter(
    "nova_search_reconciliation_errors_total", "AI Search reconciliation failures."
)
SEARCH_BUILD_ERRORS = Counter(
    "nova_search_build_errors_total", "AI Search build attempts that failed."
)
SEARCH_BUILDS_ACTIVE = Gauge("nova_search_builds_active", "AI Search builds currently executing.")
SEARCH_BUILDS = Counter(
    "nova_search_builds_total", "AI Search build attempts by outcome.", ("status",)
)
SEARCH_BUILD_DURATION = Histogram(
    "nova_search_build_duration_seconds",
    "AI Search build attempt duration in seconds.",
    ("status",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300, 1800, 3600),
)

SERVICE_UP = Gauge("nova_service_up", "Whether a Nova process completed startup.", ("service",))
SERVICE_HEARTBEAT = Gauge(
    "nova_service_last_heartbeat_timestamp_seconds",
    "Unix time of the last completed Nova service loop.",
    ("service",),
)

_DEFAULT_PORTS = {"scheduler": 9101, "worker": 9102, "agent-worker": 9103, "proxy": 9104}


def start_metrics_server(service: str) -> None:
    """Expose metrics from a standalone process on its dedicated internal port."""
    if service not in _DEFAULT_PORTS:
        raise ValueError(f"unknown metrics service: {service}")
    env_name = f"NOVA_METRICS_{service.upper().replace('-', '_')}_PORT"
    port = int(os.getenv(env_name, str(_DEFAULT_PORTS[service])))
    host = os.getenv("NOVA_METRICS_HOST", "0.0.0.0")
    start_http_server(port, addr=host)
    SERVICE_UP.labels(service=service).set(1)
    heartbeat(service)


def heartbeat(service: str) -> None:
    """Record loop progress without IDs, usernames, SQL, or other sensitive labels."""
    SERVICE_HEARTBEAT.labels(service=service).set(time.time())
