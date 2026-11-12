"""Container-local health of one worker role.

Liveness (default): the role started and its heartbeat is fresh. ``--ready``
also requires the role to reach ``nova_control`` at its published epoch.
"""

from __future__ import annotations

import argparse
import time
from urllib.request import urlopen

from prometheus_client.parser import text_string_to_metric_families

from app.core.config import settings
from app.observability.metrics import metrics_port
from app.worker.__main__ import METRICS_SERVICES


def healthy(
    body: str, service: str, *, now: float, stale_seconds: float, ready: bool = False,
) -> bool:
    values = {
        sample.name: sample.value
        for family in text_string_to_metric_families(body)
        for sample in family.samples
        if sample.labels == {"service": service}
    }
    stamp = values.get("nova_service_last_heartbeat_timestamp_seconds", 0)
    if ready and values.get("nova_metadata_ready") != 1:
        return False
    return (
        values.get("nova_service_up") == 1
        and stamp > 0
        and 0 <= now - stamp <= stale_seconds
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=METRICS_SERVICES)
    parser.add_argument("--ready", action="store_true", help="Also require metadata readiness")
    args = parser.parse_args()
    service = METRICS_SERVICES[args.role]
    try:
        with urlopen(f"http://127.0.0.1:{metrics_port(service)}/metrics", timeout=2) as response:
            body = response.read(2_000_000).decode()
        ok = healthy(
            body, service, now=time.time(),
            stale_seconds=settings.WORKER_PROCESS_STALE_SECONDS, ready=args.ready,
        )
    except Exception:
        ok = False
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
