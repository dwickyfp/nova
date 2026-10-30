"""Thumbnail digests partitioned by Nova principal, role and version (S9).

Superset caches a thumbnail under its digest. The default digest names only the
executor, so one user's thumbnail under one active role could be served under
another. These replace it with the same inputs plus the Nova session the
thumbnail is drawn for; without a Nova session there is no thumbnail.
"""

from __future__ import annotations

from typing import Any

from superset.utils.hashing import hash_from_str

from nova_superset import context


def chart_digest(chart: Any, executor_type: Any, executor: str) -> str | None:
    partition = context.partition_key()
    if partition is None:
        return None
    return hash_from_str(f"{chart.params or ''}.{executor}.{executor_type}\n{partition}")


def dashboard_digest(dashboard: Any, executor_type: Any, executor: str) -> str | None:
    partition = context.partition_key()
    if partition is None:
        return None
    return hash_from_str(
        f"{dashboard.id}\n{sorted(dashboard.charts)}\n{dashboard.position_json}\n"
        f"{dashboard.css}\n{dashboard.json_metadata}\n{executor}.{executor_type}\n{partition}"
    )
