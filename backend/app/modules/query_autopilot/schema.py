"""Compatibility readiness check for operational Autopilot scripts.

The one-shot metadata migrator owns Autopilot and audit DDL. Callers must
already have opened their workload's metadata pool on the published epoch.
"""

from app.core.metadata_db import MetadataUnavailable
from app.core.metadata_runtime import metadata_db


async def ensure_schema() -> None:
    if not await metadata_db().probe():
        raise MetadataUnavailable()
