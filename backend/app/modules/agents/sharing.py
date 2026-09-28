"""Share a Studio conversation or dashboard without sharing anyone's data rights.

A share grants *visibility of the work*, never of the owner's rows. A shared
thread is a snapshot of the text and the governed plans behind each answer with
every result row removed; a shared dashboard is its layout. Opening either
re-runs each result as the viewer, so the viewer sees exactly what their own
StarRocks role and Ranger policies allow, which may be nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.core.database import db

SHARES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STUDIO_SHARES (
    share_id VARCHAR(64) NOT NULL,
    object_type VARCHAR(16) NOT NULL,
    object_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    target_type VARCHAR(8) NOT NULL,
    target_name VARCHAR(128) NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(share_id)
DISTRIBUTED BY HASH(share_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""
_COLUMNS = "share_id, object_type, object_id, owner_name, target_type, target_name, created_at"
MAX_SHARES_PER_OBJECT = 50

#: Keys whose values are result data. They are removed from shared snapshots.
_DATA_KEYS = frozenset({"rows", "data", "values", "table", "result", "results", "preview"})


class ShareCreate(BaseModel):
    object_type: Literal["thread", "dashboard"]
    object_id: str = Field(min_length=1, max_length=64)
    target_type: Literal["user", "role"]
    target_name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][\w.\-]*$")


def _row(values: list[Any]) -> dict[str, Any]:
    record = dict(zip(_COLUMNS.split(", "), values, strict=True))
    record["created_at"] = str(record["created_at"])
    return record


def viewer_targets(user: dict[str, Any]) -> tuple[str, list[str]]:
    roles = [str(role) for role in (user.get("assigned_roles") or user.get("roles") or [])]
    return str(user["username"]), roles


def strip_rows(value: Any) -> Any:
    """A copy of a stored step trace with every result payload removed."""
    if isinstance(value, dict):
        return {
            key: strip_rows(item) for key, item in value.items() if key not in _DATA_KEYS
        }
    if isinstance(value, list):
        return [strip_rows(item) for item in value]
    return value


def shared_steps(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Keep the governed plan of each semantic step; drop everything with rows."""
    output = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        kept = strip_rows(step)
        trace = step.get("trace_detail") or {}
        if step.get("kind") == "tool" and step.get("name") == "semantic_query":
            kept["trace_detail"] = {
                key: trace.get(key)
                for key in ("question", "semantic_view", "semantic_plan", "metrics")
                if key in trace
            }
        output.append(kept)
    return output


class ShareRepository:
    async def ensure_schema(self) -> None:
        await db.execute_system(SHARES_DDL)

    async def create(self, *, owner_name: str, body: ShareCreate) -> dict[str, Any]:
        await self.ensure_schema()
        existing = await self.for_object(body.object_type, body.object_id, owner_name=owner_name)
        for share in existing:
            if (share["target_type"], share["target_name"]) == (body.target_type,
                                                                 body.target_name):
                return share
        if len(existing) >= MAX_SHARES_PER_OBJECT:
            raise ValueError(f"At most {MAX_SHARES_PER_OBJECT} shares per object.")
        share_id = str(uuid4())
        await db.execute_system(
            f"INSERT INTO NOVA_SYSTEM.CONFIG_STUDIO_SHARES ({_COLUMNS}) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s)",
            [share_id, body.object_type, body.object_id, owner_name, body.target_type,
             body.target_name, datetime.now(UTC).replace(tzinfo=None)],
        )
        return {
            "share_id": share_id, **body.model_dump(), "owner_name": owner_name,
            "created_at": None,
        }

    async def for_object(
        self, object_type: str, object_id: str, *, owner_name: str
    ) -> list[dict[str, Any]]:
        await self.ensure_schema()
        result = await db.execute_system(
            f"SELECT {_COLUMNS} FROM NOVA_SYSTEM.CONFIG_STUDIO_SHARES "
            "WHERE object_type = %s AND object_id = %s AND owner_name = %s",
            [object_type, object_id, owner_name],
        )
        return [_row(row) for row in result.get("rows") or []]

    async def delete(self, share_id: str, *, owner_name: str) -> None:
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_STUDIO_SHARES WHERE share_id = %s AND owner_name = %s",
            [share_id, owner_name],
        )

    async def visible(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        """Shares addressed to this user or to one of their assigned roles."""
        await self.ensure_schema()
        username, roles = viewer_targets(user)
        clauses = ["(target_type = 'user' AND target_name = %s)"]
        params: list[Any] = [username]
        if roles:
            clauses.append(
                "(target_type = 'role' AND target_name IN ("
                + ", ".join(["%s"] * len(roles)) + "))"
            )
            params.extend(roles)
        result = await db.execute_system(
            f"SELECT {_COLUMNS} FROM NOVA_SYSTEM.CONFIG_STUDIO_SHARES WHERE "
            + " OR ".join(clauses) + " ORDER BY created_at DESC LIMIT 500",
            params,
        )
        return [_row(row) for row in result.get("rows") or []]

    async def grant_for(
        self, user: dict[str, Any], object_type: str, object_id: str
    ) -> dict[str, Any] | None:
        return next(
            (share for share in await self.visible(user)
             if share["object_type"] == object_type and share["object_id"] == object_id),
            None,
        )


share_repository = ShareRepository()
