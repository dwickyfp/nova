"""Low-cardinality metrics shared by Nova's HTTP and standalone processes."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
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

STREAM_CAPTURE_COMMITS = Counter(
    "nova_stream_capture_commits_total",
    "Captured commits to stream sources by write kind.",
    ("kind",),
)
STREAM_CAPTURE_ROWS = Counter(
    "nova_stream_capture_rows_total", "Row changes journaled for stream sources."
)
STREAM_CAPTURE_FAILURES = Counter(
    "nova_stream_capture_failures_total",
    "Writes to stream sources that were refused or failed, by error code.",
    ("code",),
)
STREAM_CAPTURE_DURATION = Histogram(
    "nova_stream_capture_duration_seconds",
    "Captured write duration in seconds, lock wait included.",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 120),
)
STREAM_SOURCE_BLOCKS = Counter(
    "nova_stream_source_blocks_total",
    "Stream sources marked BLOCKED or SOURCE_INVALID, by new status.",
    ("status",),
)
STREAM_READS = Counter(
    "nova_stream_reads_total", "Stream reads (peeks and consumptions) by outcome.", ("result",)
)
STREAM_READ_DURATION = Histogram(
    "nova_stream_read_duration_seconds",
    "Time to resolve, verify and reduce one stream read.",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
STREAM_REDUCER_ROWS = Counter(
    "nova_stream_reducer_rows_total",
    "Journal events into and stream rows out of the reducer.",
    ("side",),
)
STREAM_CONSUMPTIONS = Counter(
    "nova_stream_consumptions_total",
    "Consuming INSERT ... SELECT outcomes (committed, empty, failed, conflict).",
    ("result",),
)
STREAM_OBJECTS = Gauge(
    "nova_streams", "Current streams by status, from the last maintenance pass.", ("status",)
)
STREAM_SOURCES = Gauge(
    "nova_stream_sources",
    "Current stream sources by status, from the last maintenance pass.",
    ("status",),
)
STREAM_BACKLOG_COMMITS_MAX = Gauge(
    "nova_stream_backlog_commits_max", "Largest number of unconsumed commits of any stream."
)
STREAM_RETENTION_REMAINING_MIN = Gauge(
    "nova_stream_retention_seconds_remaining_min",
    "Seconds until the next stream becomes stale; -1 when no stream has a backlog.",
)
STREAM_STALE_MARKED = Counter(
    "nova_stream_stale_total", "Streams marked STALE because their backlog outlived retention."
)
STREAM_TRIGGER_RUNS = Counter(
    "nova_stream_trigger_runs_total", "Graph runs created because a stream had data."
)
STREAM_TRIGGER_ERRORS = Counter(
    "nova_stream_trigger_errors_total",
    "Stream-triggered tasks whose WHEN could not be evaluated, by error code.",
    ("code",),
)
STREAM_NOT_CONSUMED = Gauge(
    "nova_stream_tasks_not_consuming",
    "Stream-triggered tasks whose last successful run left the same data pending.",
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

STUDIO_WORKFLOW_OPERATIONS = Counter(
    "nova_studio_workflow_operations_total",
    "Governed Studio operations by bounded component, operation, and outcome.",
    ("component", "operation", "status"),
)
STUDIO_FIRST_USEFUL_RESPONSE = Histogram(
    "nova_studio_first_useful_response_seconds",
    "Time until the shared engine emits its first answer content.",
    ("mode",),
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600),
)
STUDIO_WORKFLOW_DURATION = Histogram(
    "nova_studio_workflow_duration_seconds",
    "Governed Studio operation duration, excluding arguments and source content.",
    ("component", "operation", "status"),
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 30, 120, 300),
)


@contextmanager
def studio_operation(component: str, operation: str):
    if component not in {
        "release",
        "quality",
        "mission",
        "resource",
        "action",
        "workspace",
        "ui",
        "report",
    }:
        raise ValueError("Unknown Studio metric component")
    if operation not in {
        "capture",
        "evaluate",
        "score",
        "project",
        "grant",
        "execute",
        "verify",
        "validate",
        "repair",
        "fallback",
        "state",
    }:
        raise ValueError("Unknown Studio metric operation")
    started = time.perf_counter()
    status = "failed"
    try:
        yield
        status = "completed"
    finally:
        STUDIO_WORKFLOW_OPERATIONS.labels(component, operation, status).inc()
        STUDIO_WORKFLOW_DURATION.labels(component, operation, status).observe(
            time.perf_counter() - started
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

LLM_FUNCTION_REQUESTS = Counter(
    "nova_llm_function_requests_total",
    "AI_* SQL function calls served by the internal LLM gateway.",
    ("function", "status"),
)
LLM_FUNCTION_TOKENS = Counter(
    "nova_llm_function_tokens_total",
    "Provider tokens used by AI_* SQL functions.",
    ("function", "kind"),
)
QUERY_ADMISSION_ACTIVE = Gauge(
    "nova_query_admission_active", "Statements this process is running under the admission cap."
)
QUERY_ADMISSION_REFUSED = Counter(
    "nova_query_admission_refused_total",
    "Statements refused because the process was at its admission cap.",
    ("source",),
)
SERVICE_UP = Gauge("nova_service_up", "Whether a Nova process completed startup.", ("service",))
SERVICE_HEARTBEAT = Gauge(
    "nova_service_last_heartbeat_timestamp_seconds",
    "Unix time of the last completed Nova service loop.",
    ("service",),
)

_DEFAULT_PORTS = {
    "scheduler": 9101,
    "worker": 9102,
    "agent-worker": 9103,
    "proxy": 9104,
    "ingestion-worker": 9105,
    "ml-worker": 9106,
    "notebook-worker": 9107,
    "automation-worker": 9108,
    "autopilot-worker": 9109,
}


def metrics_port(service: str) -> int:
    if service not in _DEFAULT_PORTS:
        raise ValueError(f"unknown metrics service: {service}")
    env_name = f"NOVA_METRICS_{service.upper().replace('-', '_')}_PORT"
    return int(os.getenv(env_name, str(_DEFAULT_PORTS[service])))


def start_metrics_server(service: str) -> None:
    """Expose metrics from a standalone process on its dedicated internal port."""
    port = metrics_port(service)
    host = os.getenv("NOVA_METRICS_HOST", "0.0.0.0")
    start_http_server(port, addr=host)
    SERVICE_UP.labels(service=service).set(1)
    heartbeat(service)


def heartbeat(service: str) -> None:
    """Record loop progress without IDs, usernames, SQL, or other sensitive labels."""
    SERVICE_HEARTBEAT.labels(service=service).set(time.time())


REPORT_WIDGET_RESULTS = Counter(
    "nova_report_widget_results_total",
    "Governed report widget outcomes without source identifiers or data.",
    ("widget_type", "status"),
)
