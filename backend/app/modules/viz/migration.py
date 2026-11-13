"""Read historical tile dashboards and remove their source after conversion.

The one-shot importer owns type conversion. Runtime calls use the caller's
PG scope and never create tables, rewrite historical types, or ignore outages.
"""

from __future__ import annotations

import json
from typing import Any

from app.modules.agents.artifact_repository import _require_owner
from app.modules.agents.metadata_repository import agent_metadata


async def legacy_tile_dashboards(owner: str) -> list[dict[str, Any]]:
    """``owner``'s tile dashboards that have no artifact yet.

    Each comes back with the deterministic artifact id it converts to, its
    title and its zones (sheet zones name chart artifacts).
    """
    from app.modules.viz.service import legacy_dashboard_artifact_id, zones_from_legacy_tiles

    _require_owner(owner)
    async with agent_metadata(read_only=True) as store:
        rows = await store.legacy_dashboards()
        artifacts = {row["artifact_id"] for row in await store.list_artifacts()}
    pending = []
    for row in rows:
        dashboard_id, title, layout_json = (
            row[key] for key in ("dashboard_id", "title", "layout_json")
        )
        artifact_id = legacy_dashboard_artifact_id(str(dashboard_id))
        if artifact_id in artifacts:
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

    _require_owner(owner)
    forgotten = 0
    async with agent_metadata(owner) as store:
        for row in await store.legacy_dashboards():
            if legacy_dashboard_artifact_id(row["dashboard_id"]) == artifact_id:
                forgotten += int(await store.forget_legacy_dashboard(row["dashboard_id"]))
    return forgotten
