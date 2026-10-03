"""Autopilot metadata I/O, with bounded pages and optimistic version updates."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import db
from app.modules.query_autopilot.models import Observation, utcnow
from app.modules.query_autopilot.schema import TABLES


def _time(value: datetime | None) -> datetime | None:
    return value.astimezone(UTC).replace(tzinfo=None) if value else None


def _payload(value: Any) -> dict:
    return json.loads(value) if isinstance(value, str) else value


class AutopilotRepository:
    async def get(self, kind: str, identifier: str) -> dict | None:
        result = await db.execute_system(
            f"SELECT payload FROM NOVA_SYSTEM.{TABLES[kind]} WHERE id=%s", (identifier,)
        )
        return _payload(result["rows"][0][0]) if result["rows"] else None

    async def put(
        self,
        kind: str,
        identifier: str,
        payload: dict,
        *,
        family_id: str = "",
        cohort_id: str = "",
        version: int = 1,
        state: str = "",
        expires_at: datetime | None = None,
        created_at: datetime | None = None,
        expected_version: int | None = None,
        insert_only: bool = False,
    ) -> bool:
        table = TABLES[kind]
        serialized = json.dumps(payload, separators=(",", ":"), default=str, allow_nan=False)
        now = _time(utcnow())
        if expected_version is not None:
            result = await db.execute_system(
                (
                    f"UPDATE NOVA_SYSTEM.{table} SET payload=%s, version=%s, "
                    f"state=%s, updated_at=%s, expires_at=%s WHERE id=%s AND "
                    f"version=%s"
                ),
                (serialized, version, state, now, _time(expires_at), identifier, expected_version),
            )
            return result.get("affected", 0) > 0
        values: tuple[Any, ...] = (
            identifier,
            family_id,
            cohort_id,
            version,
            state,
            serialized,
            _time(created_at) or now,
            now,
            _time(expires_at),
        )
        selection = (
            "SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s"
            if insert_only
            else "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        )
        if insert_only:
            selection += f" WHERE NOT EXISTS (SELECT 1 FROM NOVA_SYSTEM.{table} WHERE id=%s)"
            values += (identifier,)
        result = await db.execute_system(
            (
                f"INSERT INTO NOVA_SYSTEM.{table} "
                "(id,family_id,cohort_id,version,state,payload,created_at,updated_at,expires_at) "
                f"{selection}"
            ),
            values,
        )
        return result.get("affected", 0) > 0

    async def page(
        self,
        kind: str,
        *,
        after: str = "",
        limit: int = 100,
        family_id: str | None = None,
        cohort_id: str | None = None,
        state: str | None = None,
        since: datetime | None = None,
        created_since: datetime | None = None,
        before: datetime | None = None,
    ) -> list[dict]:
        clauses = ["id>%s"]
        params: list[Any] = [after]
        for column, value in (("family_id", family_id), ("cohort_id", cohort_id), ("state", state)):
            if value is not None:
                clauses.append(f"{column}=%s")
                params.append(value)
        if since:
            clauses.append("updated_at>=%s")
            params.append(_time(since))
        if created_since:
            clauses.append("created_at>=%s")
            params.append(_time(created_since))
        if before:
            clauses.append("created_at<%s")
            params.append(_time(before))
        params.append(max(1, min(limit, 1000)))
        result = await db.execute_system(
            (
                f"SELECT payload FROM NOVA_SYSTEM.{TABLES[kind]} WHERE {' AND '.join(clauses)} "
                f"ORDER BY id LIMIT %s"
            ),
            params,
        )
        return [_payload(row[0]) for row in result["rows"]]

    async def observations(self, observations: list[Observation]) -> None:
        if not observations:
            return
        if len(observations) > 128:
            raise ValueError("Observation batch exceeds bound")
        rows: list[str] = []
        params: list[Any] = []
        for item in observations:
            rows.append("(%s,%s,%s,1,%s,%s,%s,%s,%s)")
            params.extend(
                (
                    item.id,
                    item.family_id,
                    item.scope.cohort_id,
                    item.status,
                    item.model_dump_json(),
                    _time(item.observed_at),
                    _time(utcnow()),
                    _time(item.observed_at + timedelta(days=7)),
                )
            )
        await db.execute_system(
            (
                f"INSERT INTO NOVA_SYSTEM.{TABLES['observations']} "
                "(id,family_id,cohort_id,version,state,payload,created_at,updated_at,expires_at) "
                f"VALUES {','.join(rows)}"
            ),
            params,
        )

    async def delete(self, kind: str, identifier: str) -> None:
        await db.execute_system(
            f"DELETE FROM NOVA_SYSTEM.{TABLES[kind]} WHERE id=%s", (identifier,)
        )

    async def cleanup_before(self, kind: str, before: datetime) -> None:
        if kind not in {"observations", "rollups", "baselines"}:
            raise ValueError("Durable decisions require explicit administrative cleanup")
        await db.execute_system(
            f"DELETE FROM NOVA_SYSTEM.{TABLES[kind]} WHERE created_at<%s", (_time(before),)
        )


repository = AutopilotRepository()
