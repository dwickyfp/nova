"""Owner-scoped Studio dashboards stored in NOVA_SYSTEM."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.database import db
from app.modules.agents.studio_schemas import DashboardLayout

DASHBOARDS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STUDIO_DASHBOARDS (
    dashboard_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    title VARCHAR(256) NOT NULL,
    layout_json JSON,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(dashboard_id)
DISTRIBUTED BY HASH(dashboard_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

_COLUMNS = "dashboard_id, owner_name, title, layout_json, created_at, updated_at"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    raise ValueError("Invalid dashboard timestamp")


def _row(values: list[object] | tuple[object, ...]) -> dict[str, Any]:
    dashboard_id, owner_name, title, layout_json, created_at, updated_at = values
    if isinstance(layout_json, str):
        layout_json = json.loads(layout_json)
    return {
        "dashboard_id": str(dashboard_id),
        "owner_name": str(owner_name),
        "title": str(title),
        "layout": DashboardLayout.model_validate(layout_json or {"tiles": []}),
        "created_at": _datetime(created_at),
        "updated_at": _datetime(updated_at),
    }


class DashboardRepository:
    async def ensure_schema(self) -> None:
        await db.execute_system(DASHBOARDS_DDL)

    async def list(self, *, owner_name: str) -> list[dict[str, Any]]:
        result = await db.execute_system(
            f"SELECT {_COLUMNS} FROM NOVA_SYSTEM.CONFIG_STUDIO_DASHBOARDS "
            "WHERE owner_name = %s ORDER BY updated_at DESC",
            [owner_name],
        )
        return [_row(row) for row in result["rows"]]

    async def get(self, dashboard_id: str, *, owner_name: str) -> dict[str, Any] | None:
        result = await db.execute_system(
            f"SELECT {_COLUMNS} FROM NOVA_SYSTEM.CONFIG_STUDIO_DASHBOARDS "
            "WHERE dashboard_id = %s AND owner_name = %s",
            [dashboard_id, owner_name],
        )
        return _row(result["rows"][0]) if result["rows"] else None

    async def create(self, *, owner_name: str, title: str) -> dict[str, Any]:
        dashboard_id = str(uuid4())
        now = _now()
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_STUDIO_DASHBOARDS "
            "(dashboard_id, owner_name, title, layout_json, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [dashboard_id, owner_name, title, '{"tiles":[]}', now, now],
        )
        created = await self.get(dashboard_id, owner_name=owner_name)
        assert created is not None
        return created

    async def update(
        self,
        dashboard_id: str,
        *,
        owner_name: str,
        title: str,
        layout: DashboardLayout,
        expected_updated_at: datetime,
    ) -> dict[str, Any] | None:
        result = await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_STUDIO_DASHBOARDS "
            "SET title = %s, layout_json = %s, updated_at = %s "
            "WHERE dashboard_id = %s AND owner_name = %s AND updated_at = %s",
            [
                title,
                layout.model_dump_json(),
                _now(),
                dashboard_id,
                owner_name,
                _datetime(expected_updated_at),
            ],
        )
        if not result.get("affected", 0):
            return None
        return await self.get(dashboard_id, owner_name=owner_name)

    async def delete(self, dashboard_id: str, *, owner_name: str) -> bool:
        result = await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_STUDIO_DASHBOARDS "
            "WHERE dashboard_id = %s AND owner_name = %s",
            [dashboard_id, owner_name],
        )
        return bool(result.get("affected", 0))


dashboard_repository = DashboardRepository()
