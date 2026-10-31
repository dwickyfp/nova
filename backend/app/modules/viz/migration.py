"""Boot migrations and legacy readers for artifacts saved before BI.

Two idempotent pieces remain once Studio artifacts live in Superset
(docs/arch-17-superset.md):

1. legacy ``table`` artifacts become ``chart`` artifacts — a chart whose
   worksheet is absent renders its SQL as a result grid, so no data is lost;
2. :func:`legacy_tile_dashboards` reads tile dashboards from before the viz
   core (``CONFIG_STUDIO_DASHBOARDS``) that never became artifacts, for the BI
   conversion to place directly; :func:`forget_tile_dashboard` removes one
   when its artifact is deleted.

Nothing here writes a viz spec.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.database import db

logger = logging.getLogger(__name__)

ARTIFACTS_TABLE = "NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS"
DASHBOARDS_TABLE = "NOVA_SYSTEM.CONFIG_STUDIO_DASHBOARDS"


async def migrate_artifact_types() -> int:
    """Legacy ``table`` artifacts join the unified model as charts."""
    result = await db.execute_system(
        f"UPDATE {ARTIFACTS_TABLE} SET artifact_type = 'chart' WHERE artifact_type = 'table'"
    )
    return int(result.get("affected", 0) or 0)


async def legacy_tile_dashboards(owner: str) -> list[dict[str, Any]]:
    """``owner``'s tile dashboards that have no artifact yet.

    Each comes back with the deterministic artifact id it converts to, its
    title and its zones (sheet zones name chart artifacts).
    """
    from app.modules.viz.service import legacy_dashboard_artifact_id, zones_from_legacy_tiles

    try:
        rows = await db.execute_system(
            f"SELECT dashboard_id, title, layout_json FROM {DASHBOARDS_TABLE} "
            "WHERE owner_name = %s",
            [owner],
        )
    except Exception as exc:  # table absent on fresh installs
        logger.info("Legacy dashboards table unavailable: %s", exc)
        return []
    pending = []
    for dashboard_id, title, layout_json in rows["rows"]:
        artifact_id = legacy_dashboard_artifact_id(str(dashboard_id))
        exists = await db.execute_system(
            f"SELECT artifact_id FROM {ARTIFACTS_TABLE} WHERE artifact_id = %s", [artifact_id]
        )
        if exists["rows"]:
            continue
        try:
            layout = json.loads(layout_json) if isinstance(layout_json, str) else layout_json
        except (ValueError, TypeError):
            layout = None
        tiles = layout.get("tiles", []) if isinstance(layout, dict) else []
        pending.append(
            {
                "artifact_id": artifact_id,
                "title": str(title or "Dashboard")[:256],
                "zones": zones_from_legacy_tiles(list(tiles)),
            }
        )
    return pending


async def forget_tile_dashboard(owner: str, artifact_id: str) -> int:
    """Delete the tile dashboard ``artifact_id`` was migrated from, if any.

    Without this, deleting such an artifact would bring the tile dashboard
    back as one waiting for conversion.
    """
    from app.modules.viz.service import legacy_dashboard_artifact_id

    try:
        rows = await db.execute_system(
            f"SELECT dashboard_id FROM {DASHBOARDS_TABLE} WHERE owner_name = %s", [owner]
        )
    except Exception as exc:  # table absent on fresh installs
        logger.info("Legacy dashboards table unavailable: %s", exc)
        return 0
    forgotten = 0
    for (dashboard_id,) in rows["rows"]:
        if legacy_dashboard_artifact_id(str(dashboard_id)) == artifact_id:
            await db.execute_system(
                f"DELETE FROM {DASHBOARDS_TABLE} WHERE dashboard_id = %s AND owner_name = %s",
                [dashboard_id, owner],
            )
            forgotten += 1
    return forgotten


async def migrate_viz() -> dict[str, Any]:
    """Run the boot migrations; safe to call on every boot."""
    tables_migrated = await migrate_artifact_types()
    if tables_migrated:
        logger.info("Viz migration: %d table artifacts became charts", tables_migrated)
    return {"tables_migrated": tables_migrated}
