"""Boot migrations that bring Studio onto the unified artifact model.

Three one-time (and idempotent) steps:

1. add the ``viz_spec`` column to ``CONFIG_STUDIO_ARTIFACTS``;
2. legacy ``table`` artifacts become ``chart`` artifacts — a chart whose
   worksheet is absent renders its SQL as a result grid, so no data is lost;
3. legacy tile dashboards (``CONFIG_STUDIO_DASHBOARDS``) become dashboard
   artifacts with deterministic ids, so a restart never duplicates them.

The legacy dashboard table is read only here; after migration nothing in the
runtime reads or writes it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.database import db

logger = logging.getLogger(__name__)

ARTIFACTS_TABLE = "NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS"
DASHBOARDS_TABLE = "NOVA_SYSTEM.CONFIG_STUDIO_DASHBOARDS"


async def ensure_viz_columns() -> bool:
    """Add ``viz_spec`` to the artifacts table when missing. True when added."""
    described = await db.execute_system(f"DESCRIBE {ARTIFACTS_TABLE}")
    existing = {str(row[0]) for row in described["rows"]}
    if "viz_spec" in existing:
        return False
    await db.execute_system(f"ALTER TABLE {ARTIFACTS_TABLE} ADD COLUMN viz_spec JSON")
    return True


async def migrate_artifact_types() -> int:
    """Legacy ``table`` artifacts join the unified model as charts."""
    result = await db.execute_system(
        f"UPDATE {ARTIFACTS_TABLE} SET artifact_type = 'chart' WHERE artifact_type = 'table'"
    )
    return int(result.get("affected", 0) or 0)


async def migrate_legacy_dashboards() -> dict[str, int]:
    """Convert every legacy tile dashboard into a dashboard artifact."""
    scanned = migrated = skipped = 0
    try:
        rows = await db.execute_system(
            f"SELECT dashboard_id, owner_name, title, layout_json FROM {DASHBOARDS_TABLE}"
        )
    except Exception as exc:  # table absent on fresh installs
        logger.info("Legacy dashboards table unavailable: %s", exc)
        return {"scanned": 0, "migrated": 0, "skipped": 0}
    if not rows["rows"]:
        return {"scanned": 0, "migrated": 0, "skipped": 0}

    from app.modules.viz.service import legacy_dashboard_artifact_id, zones_from_legacy_tiles
    from app.modules.viz.spec import DashboardSpec

    owners: dict[str, set[str]] = {}
    for dashboard_id, owner, title, layout_json in rows["rows"]:
        scanned += 1
        artifact_id = legacy_dashboard_artifact_id(str(dashboard_id))
        exists = await db.execute_system(
            f"SELECT artifact_id FROM {ARTIFACTS_TABLE} WHERE artifact_id = %s AND owner_name = %s",
            [artifact_id, owner],
        )
        if exists["rows"]:
            skipped += 1
            continue
        try:
            layout = json.loads(layout_json) if isinstance(layout_json, str) else layout_json
        except (ValueError, TypeError):
            layout = None
        tiles = layout.get("tiles", []) if isinstance(layout, dict) else []
        zones = zones_from_legacy_tiles(list(tiles))
        if owner not in owners:
            owned = await db.execute_system(
                "SELECT artifact_id FROM "
                f"{ARTIFACTS_TABLE} WHERE owner_name = %s AND artifact_type = 'chart'",
                [owner],
            )
            owners[owner] = {str(row[0]) for row in owned["rows"]}
        zones = [zone for zone in zones if zone["artifact_id"] in owners[owner]]
        if not zones:
            skipped += 1
            continue
        try:
            spec = DashboardSpec.model_validate(
                {"title": str(title or "Dashboard")[:256], "zones": zones}
            )
        except ValueError:
            skipped += 1
            continue
        from datetime import UTC, datetime

        now = datetime.now(UTC).replace(tzinfo=None)
        await db.execute_system(
            f"INSERT INTO {ARTIFACTS_TABLE} ("
            "artifact_id, owner_name, agent_id, thread_id, title, artifact_type, "
            "sql_text, database_name, schema_name, chart_spec, viz_spec, "
            "created_at, updated_at"
            ") VALUES (%s, %s, NULL, NULL, %s, 'dashboard', '', NULL, NULL, NULL, %s, %s, %s)",
            [
                artifact_id,
                owner,
                str(title or "Dashboard")[:256],
                json.dumps(spec.model_dump(mode="json"), separators=(",", ":")),
                now,
                now,
            ],
        )
        migrated += 1
    return {"scanned": scanned, "migrated": migrated, "skipped": skipped}


async def migrate_viz() -> dict[str, Any]:
    """Run every viz migration; safe to call on every boot."""
    added_column = await ensure_viz_columns()
    tables_migrated = await migrate_artifact_types()
    dashboards = await migrate_legacy_dashboards()
    if added_column or dashboards["migrated"]:
        logger.info(
            "Viz migration: viz_spec column added=%s, %d table artifacts became "
            "charts, %d legacy dashboards migrated",
            added_column,
            tables_migrated,
            dashboards["migrated"],
        )
    return {"added_column": added_column, **dashboards}
