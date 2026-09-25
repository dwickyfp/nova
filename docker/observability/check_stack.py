#!/usr/bin/env python3
"""Read-only acceptance checks for Nova's Prometheus and Grafana stack."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


HERE = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = HERE.parent / ".env"
ALERTS_FILE = HERE / "prometheus" / "alerts.yml"

APP_TARGETS = {
    "nova-backend": ("backend", "nova-backend:8000", 8000),
    "nova-scheduler": ("scheduler", "nova-scheduler:9101", 9101),
    "nova-worker": ("worker", "nova-worker:9102", 9102),
    "nova-agent-worker": ("agent-worker", "nova-agent-worker:9103", 9103),
}
CORE_JOBS = ("prometheus", "starrocks-fe", "starrocks-be", "redis", "node-exporter")
PROBES = ("stage-storage", "policy-service")
DASHBOARDS = {
    "nova-overview": "nova-7c-operations-overview",
    "nova-api-sql": "nova-7c-api-and-sql",
    "nova-jobs-agents": "nova-7c-jobs-and-agents",
    "nova-starrocks": "nova-7c-starrocks-engine",
    "nova-host-system": "nova-7c-host-system",
    "nova-dependencies": "nova-7c-dependencies",
}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request: Request, fp: Any, code: int,
                         message: str, headers: Any, newurl: str) -> None:
        return None


def read_env_file(path: Path) -> dict[str, str]:
    """Read only the two Grafana credentials from a Compose-style env file."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        key, separator, value = stripped.partition("=")
        key = key.strip()
        if not separator or key not in {"GRAFANA_ADMIN_USER", "GRAFANA_ADMIN_PASSWORD"}:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key] = value
    return values


def grafana_credentials(env_file: Path | None) -> tuple[str, str | None]:
    file_values = read_env_file(env_file) if env_file is not None else {}
    user = os.environ.get("GRAFANA_ADMIN_USER", file_values.get("GRAFANA_ADMIN_USER", "admin"))
    password = os.environ.get("GRAFANA_ADMIN_PASSWORD", file_values.get("GRAFANA_ADMIN_PASSWORD"))
    return user, password


def validate_url(value: str, *, credentialed: bool) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must be an absolute HTTP or HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL must not contain credentials")
    if credentialed and parsed.scheme == "http":
        hostname = parsed.hostname
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = hostname == "localhost"
        if not loopback:
            raise ValueError("Grafana credentials require HTTPS outside localhost")


def get_json(base_url: str, path: str, timeout: float, auth: tuple[str, str] | None = None) -> Any:
    headers = {"Accept": "application/json"}
    if auth is not None:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    request = Request(base_url.rstrip("/") + path, headers=headers)
    try:
        with build_opener(NoRedirect).open(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}") from None
    except URLError:
        raise RuntimeError("connection failed") from None
    except (TimeoutError, OSError):
        raise RuntimeError("connection timed out or failed") from None
    except (ValueError, UnicodeError):
        raise RuntimeError("invalid JSON response") from None
    if isinstance(payload, dict) and payload.get("status") == "error":
        raise RuntimeError("API reported an error")
    return payload


def check_targets(payload: Any, mode: str) -> list[Check]:
    data = payload.get("data") if isinstance(payload, dict) else None
    targets = data.get("activeTargets") if isinstance(data, dict) else None
    if not isinstance(targets, list):
        return [Check("Prometheus targets", False, "invalid target response")]
    by_job: dict[str, list[dict[str, Any]]] = {}
    for target in targets:
        if isinstance(target, dict):
            labels = target.get("labels")
            job = labels.get("job") if isinstance(labels, dict) else None
            if isinstance(job, str):
                by_job.setdefault(job, []).append(target)

    checks: list[Check] = []
    for job in (*CORE_JOBS, *APP_TARGETS):
        found = by_job.get(job, [])
        healthy = bool(found) and all(target.get("health") == "up" for target in found)
        detail = f"{sum(target.get('health') == 'up' for target in found)}/{len(found)} up"
        if job in APP_TARGETS:
            _, container_instance, port = APP_TARGETS[job]
            expected = f"host.docker.internal:{port}" if mode == "host" else container_instance
            instance_ok = any(target.get("labels", {}).get("instance") == expected for target in found)
            healthy = healthy and instance_ok
            if not instance_ok:
                detail += f"; expected {expected} for {mode} mode"
        checks.append(Check(f"Scrape {job}", healthy, detail))

    for service in PROBES:
        found = [
            target for target in by_job.get("dependency-probe", [])
            if target.get("labels", {}).get("service") == service
        ]
        healthy = bool(found) and all(target.get("health") == "up" for target in found)
        checks.append(Check(f"Scrape probe {service}", healthy,
                            f"{sum(target.get('health') == 'up' for target in found)}/{len(found)} up"))

    optional_proxy = by_job.get("nova-proxy", [])
    if optional_proxy:
        healthy = all(target.get("health") == "up" for target in optional_proxy)
        checks.append(Check("Scrape standalone proxy", healthy,
                            f"{sum(target.get('health') == 'up' for target in optional_proxy)}/{len(optional_proxy)} up"))
    return checks


def check_instant_vector(payload: Any, name: str) -> Check:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return Check(name, False, "invalid query result")
    values = data.get("result")
    if data.get("resultType") != "vector" or not isinstance(values, list):
        return Check(name, False, "invalid query result")
    try:
        healthy = bool(values) and all(float(item["value"][1]) == 1.0 for item in values)
    except (KeyError, IndexError, TypeError, ValueError):
        return Check(name, False, "invalid sample")
    return Check(name, healthy, f"{len(values)} series; expected value 1")


def metric_queries() -> dict[str, str]:
    queries = {
        f"Startup {service}": f'nova_service_up{{job="{job}",service="{service}"}}'
        for job, (service, _, _) in APP_TARGETS.items()
    }
    queries["SQL proxy listener"] = 'nova_proxy_listener_up{job="nova-backend"}'
    queries["Search indexing metrics"] = 'count(nova_search_builds_active{job="nova-backend"}) > bool 0'
    queries["Search build error metrics"] = 'count(nova_search_build_errors_total{job="nova-backend"}) > bool 0'
    queries["Search indexing poll succeeded"] = 'nova_search_last_successful_poll_timestamp_seconds{job="nova-backend"} > bool 0'
    queries["Redis service"] = 'redis_up{job="redis"}'
    queries["Docker Linux CPU metrics"] = 'count(node_cpu_seconds_total{job="node-exporter",mode="idle"}) > bool 0'
    queries["Docker Linux memory metrics"] = 'min(node_memory_MemTotal_bytes{job="node-exporter"}) > bool 0'
    queries["Docker Linux filesystem metrics"] = 'count(node_filesystem_size_bytes{job="node-exporter",mountpoint=~"/|/var/lib/docker",fstype=~"btrfs|ext4|xfs|zfs|f2fs"}) > bool 0'
    queries["Docker Linux disk metrics"] = 'count(node_disk_read_bytes_total{job="node-exporter"}) > bool 0'
    queries["Docker Linux network metrics"] = 'count(node_network_receive_bytes_total{job="node-exporter"}) > bool 0'
    queries["Docker Linux load metrics"] = 'count(node_load1{job="node-exporter"}) > bool 0'
    for service in PROBES:
        queries[f"Probe {service}"] = f'probe_success{{job="dependency-probe",service="{service}"}}'
    return queries


def check_rules(payload: Any, expected: set[str]) -> list[Check]:
    data = payload.get("data") if isinstance(payload, dict) else None
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return [Check("Alert rules", False, "invalid rule response")]
    rules = [rule for group in groups if isinstance(group, dict)
             for rule in group.get("rules", []) if isinstance(rule, dict)]
    loaded = {rule.get("name") for rule in rules}
    missing = sorted(expected - loaded)
    unhealthy = sorted(str(rule.get("name")) for rule in rules
                       if rule.get("name") in expected and rule.get("health") == "err")
    return [
        Check("Alert rules loaded", not missing,
              f"{len(expected) - len(missing)}/{len(expected)} loaded"
              + (f"; missing: {', '.join(missing)}" if missing else "")),
        Check("Alert rule evaluation", not unhealthy,
              "no evaluation errors" if not unhealthy else f"errors: {', '.join(unhealthy)}"),
    ]


def expected_alerts() -> set[str]:
    names = set(re.findall(r"^\s*-\s*alert:\s*([A-Za-z][A-Za-z0-9_]*)\s*$",
                           ALERTS_FILE.read_text(encoding="utf-8"), re.MULTILINE))
    if not names:
        raise RuntimeError("local alert catalog is empty")
    return names


def check_dashboards(payload: Any) -> list[Check]:
    if not isinstance(payload, list):
        return [Check("Grafana dashboards", False, "invalid search response")]
    by_uid = {item.get("uid"): item for item in payload if isinstance(item, dict)}
    checks = []
    for uid, slug in DASHBOARDS.items():
        item = by_uid.get(uid)
        path = urlsplit(str(item.get("url", ""))).path if item else ""
        healthy = bool(item) and item.get("type") == "dash-db" and path.endswith(f"/d/{uid}/{slug}")
        checks.append(Check(f"Grafana {uid}", healthy,
                            "provisioned with expected slug" if healthy else "missing or incorrect dashboard URL"))
    return checks


def inspect_stack(prometheus_url: str, grafana_url: str, mode: str,
                  timeout: float, credentials: tuple[str, str] | None) -> list[Check]:
    checks: list[Check] = []
    try:
        checks.extend(check_targets(get_json(prometheus_url, "/api/v1/targets", timeout), mode))
    except RuntimeError as error:
        checks.append(Check("Prometheus targets", False, str(error)))
    try:
        checks.extend(check_rules(get_json(prometheus_url, "/api/v1/rules", timeout), expected_alerts()))
    except (RuntimeError, OSError) as error:
        checks.append(Check("Prometheus alert rules", False, str(error)))
    for name, query in metric_queries().items():
        path = "/api/v1/query?" + urlencode({"query": query}, quote_via=quote)
        try:
            checks.append(check_instant_vector(get_json(prometheus_url, path, timeout), name))
        except RuntimeError as error:
            checks.append(Check(name, False, str(error)))
    if credentials is None:
        checks.append(Check("Grafana credentials", False,
                            "set GRAFANA_ADMIN_PASSWORD in the environment or env file"))
    else:
        try:
            path = "/api/search?" + urlencode({"type": "dash-db", "limit": 1000})
            checks.extend(check_dashboards(get_json(grafana_url, path, timeout, credentials)))
        except RuntimeError as error:
            checks.append(Check("Grafana dashboards", False, str(error)))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("host", "container"),
                        default=os.environ.get("NOVA_METRICS_MODE", "host"))
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090")
    parser.add_argument("--grafana-url", default="http://127.0.0.1:3001")
    parser.add_argument("--env-file", type=Path,
                        default=DEFAULT_ENV_FILE if DEFAULT_ENV_FILE.is_file() else None,
                        help="Compose env file for Grafana credentials (default: docker/.env if present)")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.env_file is not None and not args.env_file.is_file():
        parser.error("--env-file does not exist")
    try:
        validate_url(args.prometheus_url, credentialed=False)
        validate_url(args.grafana_url, credentialed=True)
        user, password = grafana_credentials(args.env_file)
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))
    credentials = (user, password) if user and password else None
    checks = inspect_stack(args.prometheus_url, args.grafana_url, args.mode,
                           args.timeout, credentials)
    for check in checks:
        state = "PASS" if check.ok else "FAIL"
        print(f"[{state}] {check.name}: {check.detail}")
    failed = sum(not check.ok for check in checks)
    print(f"{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
