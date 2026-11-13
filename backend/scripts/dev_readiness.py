"""Check the processes launched by dev.sh without exposing configuration values."""

from __future__ import annotations

import argparse
import json
import time
from urllib.request import urlopen

from prometheus_client.parser import text_string_to_metric_families

from app.core.config import settings
from app.observability.metrics import metrics_port
from app.worker.__main__ import METRICS_SERVICES
from app.worker.health import healthy


def response(port: int, path: str) -> str:
    with urlopen(f"http://127.0.0.1:{port}{path}", timeout=1) as handle:
        return handle.read(2_000_000).decode("utf-8")


def pending(web: int, query: int, frontend: int | None) -> list[str]:
    unavailable = []
    for name, port in (("web", web), ("query", query)):
        try:
            ready = json.loads(response(port, "/ready"))
            if ready.get("status") != "ready" or ready.get("role") != name:
                unavailable.append(name)
        except Exception:
            unavailable.append(name)
    for role, service in {"scheduler": "scheduler", "proxy": "proxy", **METRICS_SERVICES}.items():
        try:
            body = response(metrics_port(service), "/metrics")
            values = {
                sample.name: sample.value
                for family in text_string_to_metric_families(body)
                for sample in family.samples
                if sample.labels == {"service": service}
            }
            ready = values.get("nova_service_up") == 1 and values.get("nova_metadata_ready") == 1
            if role in METRICS_SERVICES:
                ready = ready and healthy(
                    body, service, now=time.time(),
                    stale_seconds=settings.WORKER_PROCESS_STALE_SECONDS,
                )
            if not ready:
                unavailable.append(role)
        except Exception:
            unavailable.append(role)
    if frontend is not None:
        try:
            if '<div id="root">' not in response(frontend, "/"):
                unavailable.append("frontend")
        except Exception:
            unavailable.append("frontend")
    return unavailable


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web", type=int, required=True)
    parser.add_argument("--query", type=int, required=True)
    parser.add_argument("--frontend", type=int)
    args = parser.parse_args()
    missing = pending(args.web, args.query, args.frontend)
    if missing:
        print("Waiting for: " + ", ".join(missing))
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
