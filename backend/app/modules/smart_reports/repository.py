"""Append-only report records with explicit revision and fork detection."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from app.core.database import db

TABLE = "NOVA_SYSTEM.CONFIG_SMART_REPORT_EVENTS"
DDL = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
    record_id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    operation_id VARCHAR(64) NOT NULL,
    report_id VARCHAR(64) NOT NULL,
    kind VARCHAR(32) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    checksum VARCHAR(64) NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(record_id, revision, operation_id)
DISTRIBUTED BY HASH(record_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")"""


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
        ).encode()
    ).hexdigest()


def decode(row: list | tuple) -> dict:
    value = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    if digest(value) != row[2]:
        raise HTTPException(409, "Report record requires integrity reconciliation")
    return {"revision": int(row[1]), "payload": value, "checksum": row[2]}


class ReportRepository:
    async def ensure_schema(self) -> None:
        await db.execute_system(DDL)

    async def read(self, record_id: str, revision: int | None = None) -> dict | None:
        forks = await db.execute_system(
            f"SELECT revision FROM {TABLE} WHERE record_id=%s GROUP BY "
            f"revision HAVING COUNT(*)>1 LIMIT 1",
            [record_id],
        )
        if forks["rows"]:
            raise HTTPException(409, "Concurrent report revisions require reconciliation")
        clause, params = (
            (" AND revision=%s", [record_id, revision])
            if revision is not None
            else ("", [record_id])
        )
        result = await db.execute_system(
            f"SELECT payload,revision,checksum FROM {TABLE} WHERE "
            f"record_id=%s{clause} ORDER BY revision DESC LIMIT 1",
            params,
        )
        return decode(result["rows"][0]) if result["rows"] else None

    async def replay(self, record_id: str, operation_id: str, request: Any) -> dict | None:
        result = await db.execute_system(
            f"SELECT payload,revision,checksum,request_hash FROM {TABLE} "
            f"WHERE record_id=%s AND operation_id=%s LIMIT 2",
            [record_id, operation_id],
        )
        if not result["rows"]:
            return None
        if len(result["rows"]) != 1 or result["rows"][0][3] != digest(request):
            raise HTTPException(409, "Operation ID was already used for a different request")
        await self.read(record_id)
        return decode(result["rows"][0])

    async def append(
        self,
        *,
        record_id: str,
        report_id: str,
        kind: str,
        owner: str,
        operation_id: str,
        expected_revision: int,
        payload: dict,
        request: Any,
        lease: Any,
    ) -> dict:
        previous = await self.replay(record_id, operation_id, request)
        if previous:
            return previous
        current = await self.read(record_id)
        revision = current["revision"] if current else 0
        if revision != expected_revision:
            raise HTTPException(
                409, detail={"code": "REVISION_CONFLICT", "current_revision": revision}
            )
        if not await lease.renew():
            raise HTTPException(409, "Report write lease expired")
        checksum = digest(payload)
        await db.execute_system(
            f"INSERT INTO {TABLE} "
            f"(record_id,revision,operation_id,report_id,kind,owner_name,request_hash,checksum,payload,created_at)"
            f" VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [
                record_id,
                revision + 1,
                operation_id,
                report_id,
                kind,
                owner,
                digest(request),
                checksum,
                json.dumps(payload, ensure_ascii=False, default=str),
                datetime.now(UTC).replace(tzinfo=None),
            ],
        )
        written = await self.read(record_id)
        if not written or written["revision"] != revision + 1 or written["checksum"] != checksum:
            raise HTTPException(409, "Report write requires reconciliation")
        return written

    async def ids(
        self, kind: str, *, report_id: str | None = None, after: str = "", limit: int = 100
    ) -> list[str]:
        params: list[Any] = [kind, after]
        where = "kind=%s AND record_id>%s"
        if report_id:
            where += " AND report_id=%s"
            params.append(report_id)
        params.append(min(limit, 200))
        result = await db.execute_system(
            f"SELECT DISTINCT record_id FROM {TABLE} WHERE {where} ORDER BY record_id LIMIT %s",
            params,
        )
        return [str(row[0]) for row in result["rows"]]

    async def history(self, record_id: str, before: int = 2**63 - 1) -> list[dict]:
        await self.read(record_id)
        result = await db.execute_system(
            f"SELECT payload,revision,checksum FROM {TABLE} WHERE "
            f"record_id=%s AND revision<%s ORDER BY revision DESC LIMIT 50",
            [record_id, before],
        )
        return [decode(row) for row in result["rows"]]


report_repository = ReportRepository()
