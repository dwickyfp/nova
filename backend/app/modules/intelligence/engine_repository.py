"""Scoped, revision-checked persistence for the Intelligence domain."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import TypeVar

from fastapi import HTTPException

from app.core.database import db
from app.core.redis import session_store
from app.modules.intelligence.contracts import NewsItem, Record, Scope, fingerprint, utc_now
from app.modules.intelligence.engine_schema import ENGINE_TABLES
from app.modules.task_orchestration.transport import LeaderLock

R = TypeVar("R", bound=Record)


@asynccontextmanager
async def metadata_lock(key: str, *, timeout_seconds: int = 20):
    client = session_store._redis
    if client is None:
        raise HTTPException(status_code=503, detail="Metadata coordination is unavailable")
    if not 1 <= timeout_seconds <= 120:
        raise ValueError("Metadata coordination deadline must be within 1..120 seconds")
    lock = LeaderLock(
        client, key=f"nova:intelligence:write:{fingerprint(key)}",
        ttl_seconds=timeout_seconds + 10,
    )
    if not await lock.acquire():
        raise HTTPException(status_code=409, detail="This record is being updated; retry")
    try:
        async with asyncio.timeout(timeout_seconds):
            yield lock
    finally:
        await lock.release()


def _decode(value):
    return json.loads(value) if isinstance(value, str) else value


class IntelligenceRepository:
    async def recent_incident(self, candidate: NewsItem, cooldown_hours: int) -> NewsItem | None:
        from datetime import timedelta

        scope = candidate.scope
        result = await db.execute_system(
            "SELECT payload,branches FROM (SELECT payload,ROW_NUMBER() OVER "
            "(PARTITION BY id ORDER BY revision DESC) rn,"
            "COUNT(*) OVER (PARTITION BY id,revision) branches "
            f"FROM {self._table('news')} WHERE principal=%s AND active_role=%s "
            "AND security_context_version=%s) latest "
            "WHERE rn=1 AND get_json_string(CAST(payload AS VARCHAR),'$.monitor_id')=%s "
            "AND get_json_string(CAST(payload AS VARCHAR),'$.window.end') >= %s "
            "AND get_json_string(CAST(payload AS VARCHAR),'$.window.end') <= %s LIMIT 101",
            [
                scope.principal,
                scope.active_role,
                scope.security_context_version,
                candidate.monitor_id,
                (candidate.window.end - timedelta(hours=cooldown_hours))
                .isoformat()
                .replace("+00:00", "Z"),
                (candidate.window.end + timedelta(hours=cooldown_hours))
                .isoformat()
                .replace("+00:00", "Z"),
            ],
        )
        if len(result["rows"]) > 100 or any(row[1] != 1 for row in result["rows"]):
            raise HTTPException(status_code=409, detail="Incident history requires reconciliation")
        for row in result["rows"]:
            prior = NewsItem.model_validate(_decode(row[0]))
            if (
                prior.semantic == candidate.semantic
                and (prior.change > 0) == (candidate.change > 0)
                and abs(prior.window.end - candidate.window.end) < timedelta(hours=cooldown_hours)
            ):
                return prior
        return None

    @staticmethod
    def _table(kind: str) -> str:
        return "NOVA_SYSTEM." + ENGINE_TABLES[kind]

    async def get(
        self,
        kind: str,
        record_id: str,
        scope: Scope,
        model: type[R],
        *,
        revision: int | None = None,
    ) -> R | None:
        params: list = [
            record_id,
            scope.principal,
            scope.active_role,
            scope.security_context_version,
        ]
        condition = ""
        if revision is not None:
            condition = " AND revision=%s"
            params.append(revision)
        result = await db.execute_system(
            f"SELECT payload,revision FROM {self._table(kind)} WHERE id=%s AND principal=%s "
            "AND active_role=%s AND security_context_version=%s"
            + condition
            + " ORDER BY revision DESC LIMIT 2",
            params,
        )
        self._check_fork(result["rows"])
        return model.model_validate(_decode(result["rows"][0][0])) if result["rows"] else None

    async def shared_decision(self, record_id: str, owner_name: str, model: type[R]) -> R | None:
        result = await db.execute_system(
            f"SELECT payload,revision FROM {self._table('decisions')} "
            "WHERE id=%s AND principal=%s ORDER BY revision DESC LIMIT 2",
            [record_id, owner_name],
        )
        self._check_fork(result["rows"])
        return model.model_validate(_decode(result["rows"][0][0])) if result["rows"] else None

    async def related(self, kind: str, decision_id: str, scope: Scope, model: type[R]) -> list[R]:
        if kind not in {"events", "outcomes", "actions", "action_events"}:
            raise ValueError("Unsupported lineage relation")
        result = await db.execute_system(
            "SELECT payload,branches FROM (SELECT payload,ROW_NUMBER() OVER "
            "(PARTITION BY id ORDER BY revision DESC) rn,"
            f"COUNT(*) OVER (PARTITION BY id,revision) branches FROM {self._table(kind)} "
            "WHERE principal=%s AND active_role=%s AND security_context_version=%s) latest "
            "WHERE rn=1 AND get_json_string(CAST(payload AS VARCHAR),'$.decision_id')=%s LIMIT 101",
            [scope.principal, scope.active_role, scope.security_context_version, decision_id],
        )
        if len(result["rows"]) > 100:
            raise HTTPException(
                status_code=422, detail="Lineage exceeds the per-request record bound"
            )
        if any(row[1] != 1 for row in result["rows"]):
            raise HTTPException(
                status_code=409, detail="Concurrent lineage revisions require reconciliation"
            )
        return [model.model_validate(_decode(row[0])) for row in result["rows"]]

    async def action_for_review(self, record_id: str, model: type[R]) -> R | None:
        # Only the review service uses this lookup, then authorizes the linked
        # decision and the reviewer's role before returning any record data.
        result = await db.execute_system(
            f"SELECT payload,revision FROM {self._table('actions')} WHERE id=%s "
            "ORDER BY revision DESC LIMIT 2",
            [record_id],
        )
        self._check_fork(result["rows"])
        return model.model_validate(_decode(result["rows"][0][0])) if result["rows"] else None

    async def page(
        self,
        kind: str,
        scope: Scope,
        model: type[R],
        *,
        after: str = "",
        limit: int = 50,
        endpoint: str | None = None,
        search: str | None = None,
    ) -> list[R]:
        endpoint_filter = ""
        search_filter = ""
        if search is not None:
            if kind != "nodes":
                raise ValueError("Text search is only available for context nodes")
            search_filter = (
                " AND INSTR(LOWER(get_json_string(CAST(payload AS VARCHAR),'$.name')),LOWER(%s))>0"
            )
        if endpoint is not None:
            if kind != "edges":
                raise ValueError("Endpoint filtering is only available for graph edges")
            endpoint_filter = (
                " AND (get_json_string(CAST(payload AS VARCHAR),'$.source')=%s "
                "OR get_json_string(CAST(payload AS VARCHAR),'$.target')=%s)"
            )
        result = await db.execute_system(
            "SELECT payload,branches FROM (SELECT id,payload,COUNT(*) OVER "
            "(PARTITION BY id,revision) AS branches,ROW_NUMBER() OVER "
            f"(PARTITION BY id ORDER BY revision DESC) AS rn FROM {self._table(kind)} "
            "WHERE principal=%s AND active_role=%s AND security_context_version=%s "
            "AND id>%s) current_records WHERE rn=1"
            + endpoint_filter
            + search_filter
            + " ORDER BY id LIMIT %s",
            [
                scope.principal,
                scope.active_role,
                scope.security_context_version,
                after,
                *([endpoint, endpoint] if endpoint is not None else []),
                *([search] if search is not None else []),
                min(max(limit, 1), 101),
            ],
        )
        if any(row[1] != 1 for row in result["rows"]):
            raise HTTPException(
                status_code=409, detail="Concurrent revisions require reconciliation"
            )
        return [model.model_validate(_decode(row[0])) for row in result["rows"]]

    @staticmethod
    def _check_fork(rows: list) -> None:
        if len(rows) > 1 and rows[0][1] == rows[1][1]:
            raise HTTPException(
                status_code=409, detail="Concurrent revisions require reconciliation"
            )

    async def save(self, kind: str, record: R, *, expected_revision: int = 0) -> R:
        async with metadata_lock(f"{kind}:{record.id}") as lock:
            # Check the global identifier before the scoped read; a guessed ID
            # must never overwrite a record belonging to another principal.
            result = await db.execute_system(
                f"SELECT payload,revision FROM {self._table(kind)} WHERE id=%s "
                "ORDER BY revision DESC LIMIT 2",
                [record.id],
            )
            self._check_fork(result["rows"])
            if result["rows"]:
                old = type(record).model_validate(_decode(result["rows"][0][0]))
                if old.scope.model_dump(exclude={"session_id"}) != record.scope.model_dump(
                    exclude={"session_id"}
                ):
                    raise HTTPException(status_code=404, detail="Record unavailable")
                ignored = {"revision", "created_at", "updated_at"}
                if old.model_dump(exclude=ignored) == record.model_dump(exclude=ignored):
                    return old
                if old.revision != expected_revision:
                    raise HTTPException(
                        status_code=409, detail="Record changed; reload before editing"
                    )
                record = record.model_copy(update={"created_at": old.created_at})
            elif expected_revision:
                raise HTTPException(
                    status_code=409, detail="Record no longer matches this revision"
                )
            if not await lock.renew():
                raise HTTPException(status_code=409, detail="Metadata lease expired; retry")
            record = record.model_copy(
                update={"revision": expected_revision + 1, "updated_at": utc_now()}
            )
            operation_id = fingerprint(record.model_dump(mode="json"))[:32]
            await db.execute_system(
                f"INSERT INTO {self._table(kind)} "
                "(id,revision,operation_id,principal,active_role,security_context_version,"
                "payload,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())",
                [
                    record.id,
                    record.revision,
                    operation_id,
                    record.scope.principal,
                    record.scope.active_role,
                    record.scope.security_context_version,
                    record.model_dump_json(),
                ],
            )
            return record


intelligence_repository = IntelligenceRepository()
