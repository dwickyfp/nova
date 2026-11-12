"""Readiness requires the installed epoch and every launched workload."""

import json

import pytest

from scripts import dev_readiness as readiness


@pytest.fixture
def endpoints(monkeypatch):
    bodies = {
        (8000, "/ready"): json.dumps({"status": "ready", "role": "web"}),
        (8001, "/ready"): json.dumps({"status": "ready", "role": "query"}),
        (5173, "/"): '<html><div id="root"></div></html>',
    }
    for service in ("scheduler", "proxy", *readiness.METRICS_SERVICES.values()):
        bodies[readiness.metrics_port(service), "/metrics"] = (
            f'nova_service_up{{service="{service}"}} 1\n'
            f'nova_metadata_ready{{service="{service}"}} 1\n'
            f'nova_service_last_heartbeat_timestamp_seconds{{service="{service}"}} 1000\n'
        )
    monkeypatch.setattr(readiness, "response", lambda port, path: bodies[port, path])
    monkeypatch.setattr(readiness.time, "time", lambda: 1001)
    return bodies


def test_complete_topology_is_ready(endpoints):
    assert readiness.pending(8000, 8001, 5173) == []


@pytest.mark.parametrize(
    "body",
    [
        '{"status":"ready","role":"web"}',
        '{"status":"starting","role":"query"}',
        "private-malformed-response",
    ],
)
def test_query_must_report_its_own_ready_role(endpoints, body):
    endpoints[8001, "/ready"] = body
    assert readiness.pending(8000, 8001, None) == ["query"]


@pytest.mark.parametrize("fault", ["epoch", "stale", "down"])
def test_worker_needs_live_heartbeat_and_metadata(endpoints, fault):
    role, service = next(iter(readiness.METRICS_SERVICES.items()))
    key = readiness.metrics_port(service), "/metrics"
    metric = {
        "epoch": "nova_metadata_ready",
        "stale": "nova_service_last_heartbeat_timestamp_seconds",
        "down": "nova_service_up",
    }[fault]
    endpoints[key] = "\n".join(
        line.rsplit(" ", 1)[0] + " 0" if line.startswith(metric + "{") else line
        for line in endpoints[key].splitlines()
    )
    assert readiness.pending(8000, 8001, None) == [role]


def test_unreachable_processes_are_named_without_response_data(endpoints):
    del endpoints[8000, "/ready"]
    endpoints[5173, "/"] = "private-provider-error"
    assert readiness.pending(8000, 8001, 5173) == ["web", "frontend"]
