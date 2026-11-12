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

from pydantic import BaseModel, Field

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext
from app.modules.agents.metadata_repository import AgentMetadataRepository, agent_metadata

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


class ShareTarget(BaseModel):
    object_type: Literal["thread", "dashboard", "decision"]
    object_id: str = Field(min_length=1, max_length=64)
    target_type: Literal["user", "role"]
    target_name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][\w.\-]*$")


class ShareCreate(ShareTarget):
    object_type: Literal["thread", "dashboard"]


def _row(values: list[Any]) -> dict[str, Any]:
    record = dict(zip(_COLUMNS.split(", "), values, strict=True))
    created = record["created_at"]
    if isinstance(created, datetime) and created.tzinfo is not None:
        created = created.astimezone(UTC).replace(tzinfo=None)
    record["created_at"] = str(created)
    return record


def viewer_targets(user: dict[str, Any]) -> tuple[str, list[str]]:
    actor = SecurityContext.from_session(user)
    return actor.principal, [actor.active_role]


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
        # Report steps come only from threads recorded before reports were
        # removed; they stay private like UI steps.
        if step.get("kind") in {"ui", "ui_action", "smart_report", "report_context"}:
            continue
        kept = strip_rows(step)
        trace = kept.get("trace_detail") or {}
        if step.get("kind") == "tool" and step.get("name") == "semantic_query":
            kept["trace_detail"] = {
                key: trace.get(key)
                for key in ("question", "semantic_view", "semantic_plan", "metrics")
                if key in trace
            }
        output.append(kept)
    return output


def shared_messages(messages: list[dict]) -> list[dict]:
    private = False
    output = []
    for message in messages:
        steps = message.get("steps") or []
        private = private or any(
            isinstance(step, dict) and (
                step.get("kind") in {"ui_action", "report_context"}
                or step.get("kind") == "tool" and step.get("name") == "smart_report"
            ) for step in steps
        )
        if message.get("role") not in {"user", "assistant"}:
            continue
        output.append({
            **message,
            "content": ("This part uses private interactive inputs. Continue in your own "
                        "conversation." if private else message.get("content", "")),
            "steps": [] if private else shared_steps(steps),
        })
    return output


class ShareRepository:
    @staticmethod
    def _user(store: AgentMetadataRepository, user: dict) -> None:
        actor = SecurityContext.from_session(user)
        if actor != store.actor:
            raise MetadataPermissionDenied()

    async def active_grants(
        self, user: dict, *, object_type: str, object_id: str | None = None,
        owner_name: str | None = None, after: str = "", limit: int = 101,
    ) -> list[dict]:
        async with agent_metadata(read_only=True) as store:
            self._user(store, user)
            rows = await store.active_grants(object_type, object_id=object_id,
                                            owner_name=owner_name, after=after,
                                            limit=min(max(limit, 1), 101))
            return [_row([row.get(key) for key in _COLUMNS.split(", ")]) for row in rows]

    async def create(self, *, owner_name: str, body: ShareTarget) -> dict[str, Any]:
        async with agent_metadata(owner_name) as store:
            if store.actor.principal != owner_name:
                raise MetadataPermissionDenied()
            await store.lock_share_object(body.object_type, body.object_id)
            existing = await store.shares_for_object(body.object_type, body.object_id)
            matching = [row for row in existing if
                        (row["target_type"], row["target_name"]) ==
                        (body.target_type, body.target_name)]
            if len(matching) > 1:
                raise MetadataConflict()
            if matching:
                row = matching[0]
            else:
                if len(existing) >= MAX_SHARES_PER_OBJECT:
                    raise ValueError(f"At most {MAX_SHARES_PER_OBJECT} shares per object.")
                row = await store.create_share(body.object_type, body.object_id,
                                               body.target_type, body.target_name)
            return _row([row.get(key) for key in _COLUMNS.split(", ")])

    async def for_object(
        self, object_type: str, object_id: str, *, owner_name: str,
    ) -> list[dict[str, Any]]:
        async with agent_metadata(read_only=True) as store:
            if store.actor.principal != owner_name:
                raise MetadataPermissionDenied()
            return [_row([row.get(key) for key in _COLUMNS.split(", ")])
                    for row in await store.shares_for_object(object_type, object_id)]

    async def delete(self, share_id: str, *, owner_name: str) -> None:
        async with agent_metadata(owner_name) as store:
            if store.actor.principal != owner_name:
                raise MetadataPermissionDenied()
            await store.delete_share(share_id)

    async def visible(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        """Shares addressed to this user or their active role."""
        async with agent_metadata(read_only=True) as store:
            self._user(store, user)
            rows = await store.active_grants(limit=500, latest_first=True)
            return [_row([row.get(key) for key in _COLUMNS.split(", ")]) for row in rows]

    async def thread(self, user: dict, thread_id: str, *, owner_name: str) -> dict | None:
        async with agent_metadata(read_only=True) as store:
            self._user(store, user)
            return await store.shared_conversation(thread_id, owner_name)

    async def grant_for(
        self, user: dict[str, Any], object_type: str, object_id: str,
    ) -> dict[str, Any] | None:
        grants = await self.active_grants(user, object_type=object_type, object_id=object_id)
        return grants[0] if grants else None


share_repository = ShareRepository()
