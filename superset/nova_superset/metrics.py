"""Superset's request, query and Celery metrics for Nova's Prometheus.

Superset reports through a statsd ``STATS_LOGGER``; Nova adds request, query
and task timings with DogStatsD tags. Everything goes over UDP to the statsd
exporter of the observability stack (``SUPERSET_STATSD_HOST``), which
Prometheus scrapes (docs/arch-17-superset.md, Phase 5). Sending never blocks
or fails a request: when the exporter is not running, packets are dropped and
its address is looked up again a minute later.

Tags hold no user, role, SQL or object names: only the method, status class,
area of the UI, task name and outcome.
"""

from __future__ import annotations

import logging
import os
import re
import socket
import time
from typing import Any

from celery.signals import task_postrun, task_prerun
import pymysql.cursors
from flask import Flask, g, request

from superset.stats_logger import BaseStatsLogger

logger = logging.getLogger(__name__)

_RETRY_SECONDS = 60
_AREAS = frozenset(
    {
        "chart", "dashboard", "dataset", "database", "explore", "explore_json", "sqllab",
        "report", "query", "saved_query", "tablemodelview", "welcome", "log", "me",
        "security", "async_event", "menu", "tag", "theme",
    }
)  # fmt: skip


class _Udp:
    def __init__(self) -> None:
        self.host = os.environ.get("SUPERSET_STATSD_HOST", "statsd-exporter")
        self.port = int(os.environ.get("SUPERSET_STATSD_PORT", "9125"))
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._address: tuple[str, int] | None = None
        self._next_lookup = 0.0

    def send(self, line: str) -> None:
        now = time.monotonic()
        if self._address is None:
            if now < self._next_lookup:
                return
            try:
                self._address = (socket.gethostbyname(self.host), self.port)
            except OSError:
                self._next_lookup = now + _RETRY_SECONDS
                return
        try:
            self._socket.sendto(line.encode(), self._address)
        except OSError:
            self._address, self._next_lookup = None, now + _RETRY_SECONDS


_udp = _Udp()


def _tags(tags: dict[str, str]) -> str:
    return "|#" + ",".join(f"{key}:{value}" for key, value in tags.items()) if tags else ""


def timing(name: str, milliseconds: float, **tags: str) -> None:
    _udp.send(f"{name}:{milliseconds:.3f}|ms{_tags(tags)}")


class NovaStatsLogger(BaseStatsLogger):
    """``STATS_LOGGER``: Superset's own counters and timings, as statsd."""

    def __init__(self, prefix: str = "superset") -> None:
        super().__init__(prefix=prefix)

    def key(self, key: str) -> str:
        return f"{self.prefix}.{key}"

    def incr(self, key: str) -> None:
        _udp.send(f"{self.key(key)}:1|c")

    def decr(self, key: str) -> None:
        _udp.send(f"{self.key(key)}:-1|c")

    def timing(self, key: str, value: float) -> None:
        # Superset times in seconds; statsd timers are milliseconds.
        _udp.send(f"{self.key(key)}:{value * 1000:.3f}|ms")

    def gauge(self, key: str, value: float) -> None:
        _udp.send(f"{self.key(key)}:{value}|g")


def area(path: str) -> str | None:
    """The part of BI a request belongs to (a bounded set), or ``None`` for assets."""
    if path.startswith(("/static/", "/health", "/healthcheck", "/ping")):
        return None
    parts = [part for part in path.split("/") if part]
    if parts[:2] == ["api", "v1"] and len(parts) > 2:
        name = parts[2]
    elif parts[:1] == ["superset"] and len(parts) > 1:
        name = parts[1]
    else:
        name = parts[0] if parts else ""
    return name if name in _AREAS else "other"


def _started() -> None:
    g.nova_started = time.perf_counter()


def _finished(response: Any) -> Any:
    started = getattr(g, "nova_started", None)
    section = area(request.path)
    if started is not None and section is not None:
        timing(
            "nova_bi_request",
            (time.perf_counter() - started) * 1000,
            method=request.method,
            status=f"{response.status_code // 100}xx",
            area=section,
        )
    return response


def init_app(app: Flask) -> None:
    app.before_request(_started)
    app.after_request(_finished)


# ── Queries Superset runs on Nova ────────────────────────────────────
# Superset executes on raw DBAPI cursors, so SQLAlchemy's cursor events do not
# see these queries. The connection mutator gives every Nova connection this
# cursor class instead.


class TimedCursor(pymysql.cursors.Cursor):
    def execute(self, query: Any, args: Any = None) -> int:
        started = time.perf_counter()
        try:
            result = super().execute(query, args)
        except Exception:
            timing("nova_bi_query", (time.perf_counter() - started) * 1000, outcome="error")
            raise
        timing("nova_bi_query", (time.perf_counter() - started) * 1000, outcome="ok")
        return result


# ── Celery tasks ─────────────────────────────────────────────────────

_task_started: dict[str, float] = {}


@task_prerun.connect
def _task_begins(task_id: str | None = None, **_: Any) -> None:
    if task_id:
        _task_started[task_id] = time.perf_counter()


@task_postrun.connect
def _task_ends(
    task_id: str | None = None, task: Any = None, state: str | None = None, **_: Any
) -> None:
    started = _task_started.pop(task_id or "", None)
    if started is not None:
        timing(
            "nova_bi_task",
            (time.perf_counter() - started) * 1000,
            task=getattr(task, "name", "unknown"),
            state=(state or "unknown").lower(),
        )
