"""Table-to-table lineage harvested from ``AUDIT_LOG``, keyed by securable id.

Every successful statement Nova executes leaves a redacted ``AUDIT_LOG`` row
with its SQL text and session database. A pass reads the rows written since the
previous pass, keeps the statements that write one relation from a query
(:func:`app.sql_frontend.analysis.lineage.statement_lineage`), and upserts one
edge per ``(source, target)`` pair into ``NOVA_SYSTEM.CATALOG_LINEAGE``.

* Endpoints are securable ids. A name not synced yet resolves to the id the
  sync will register for it, so an edge recorded before the sync still lands on
  the right securable. A dropped endpoint keeps its edges.
* An edge stores no user, row, or literal: only the two ids, their names when
  last seen, the statement kind, and the audit query id.
* Each pass is recorded in ``CATALOG_SYNC_RUNS`` under the reserved catalog
  name ``lineage:audit``; its ``watermark`` is where the next pass starts.
  Rows are re-read with a short overlap; edges are idempotent upserts.
* Unqualified names use the row's session database and ``default_catalog``.
* The store is descriptive: callers see an edge only when both ends are
  visible to them (:mod:`.governance`).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from app.core.database import db
from app.sql_frontend.analysis.lineage import StatementLineage, statement_lineage

from .repository import row_id
from .securables import relation_id
from .sync import nova_now, redact_error

log = logging.getLogger(__name__)

_PK_PROPERTIES = 'PROPERTIES("replication_num"="1", "enable_persistent_index"="true")'

CATALOG_LINEAGE_DDL = f"""
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CATALOG_LINEAGE (
    id             VARCHAR(64) NOT NULL,
    source_id      VARCHAR(64) NOT NULL,
    target_id      VARCHAR(64) NOT NULL,
    source_name    VARCHAR(1024) NOT NULL,
    target_name    VARCHAR(1024) NOT NULL,
    statement_kind VARCHAR(32) NOT NULL,
    first_seen_at  DATETIME,
    last_seen_at   DATETIME,
    last_query_id  VARCHAR(64)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
{_PK_PROPERTIES}
"""

#: Reserved ``CATALOG_SYNC_RUNS.catalog_name`` of lineage passes. The colon
#: makes it an invalid catalog identifier, so no engine catalog can share it.
LINEAGE_RUN = "lineage:audit"

#: How far back the first pass reads.
FIRST_PASS_LOOKBACK = timedelta(days=30)
#: Rows already read are re-read this far back, for rows committed late.
OVERLAP = timedelta(minutes=10)
#: Audit rows read per pass; a busy deployment catches up over later passes.
MAX_ROWS = 5000

_COLUMNS = (
    "id", "source_id", "target_id", "source_name", "target_name", "statement_kind",
    "first_seen_at", "last_seen_at", "last_query_id",
)


def edge_id(source_id: str, target_id: str) -> str:
    return row_id(source_id, target_id, "lineage")


def _records(result: dict) -> list[dict]:
    return [dict(zip(_COLUMNS, row, strict=False)) for row in result.get("rows") or []]


class LineageRepository:
    async def ensure_schema(self) -> None:
        await db.execute_system(CATALOG_LINEAGE_DDL)

    async def edges_of(self, securable_id: str, limit: int = 200) -> list[dict]:
        result = await db.execute_system(
            "SELECT " + ", ".join(_COLUMNS) + " FROM NOVA_SYSTEM.CATALOG_LINEAGE "
            "WHERE source_id = %s OR target_id = %s ORDER BY last_seen_at DESC LIMIT %s",
            [securable_id, securable_id, int(limit)],
        )
        return _records(result)

    async def existing(self, ids: list[str]) -> dict[str, dict]:
        found: dict[str, dict] = {}
        for start in range(0, len(ids), 200):
            chunk = ids[start : start + 200]
            result = await db.execute_system(
                "SELECT " + ", ".join(_COLUMNS) + " FROM NOVA_SYSTEM.CATALOG_LINEAGE "
                f"WHERE id IN ({', '.join(['%s'] * len(chunk))})",
                chunk,
            )
            found.update({row["id"]: row for row in _records(result)})
        return found

    async def upsert(self, edges: list[dict]) -> None:
        for start in range(0, len(edges), 200):
            batch = edges[start : start + 200]
            placeholders = "(" + ", ".join(["%s"] * len(_COLUMNS)) + ")"
            await db.execute_system(
                "INSERT INTO NOVA_SYSTEM.CATALOG_LINEAGE (" + ", ".join(_COLUMNS) + ") VALUES "
                + ", ".join([placeholders] * len(batch)),
                [edge.get(column) for edge in batch for column in _COLUMNS],
            )

    async def watermark(self) -> datetime | None:
        result = await db.execute_system(
            "SELECT counts_json FROM NOVA_SYSTEM.CATALOG_SYNC_RUNS "
            "WHERE catalog_name = %s AND status = 'ok' ORDER BY started_at DESC LIMIT 1",
            [LINEAGE_RUN],
        )
        rows = result.get("rows") or []
        if not rows:
            return None
        try:
            value = json.loads(rows[0][0] or "{}").get("watermark")
            return datetime.fromisoformat(value) if value else None
        except (TypeError, ValueError, AttributeError):
            return None


lineage_repo = LineageRepository()


def _edges(lineage: StatementLineage, query_id: str | None, at: datetime) -> list[dict]:
    target = lineage.target
    target_id = relation_id(target.catalog, target.database, target.name)
    return [
        {
            "id": edge_id(source_id, target_id),
            "source_id": source_id,
            "target_id": target_id,
            "source_name": source.full_name,
            "target_name": target.full_name,
            "statement_kind": lineage.kind,
            "first_seen_at": at,
            "last_seen_at": at,
            "last_query_id": query_id,
        }
        for source in lineage.sources
        for source_id in [relation_id(source.catalog, source.database, source.name)]
    ]


async def harvest_lineage(repo: LineageRepository | None = None) -> dict[str, Any]:
    """Read new successful statements from ``AUDIT_LOG`` and record their edges."""
    from .repository import catalog_repo

    repo = repo or lineage_repo
    started = nova_now()
    run: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "catalog_name": LINEAGE_RUN,
        "trigger": "lineage",
        "status": "running",
        "started_at": started,
    }
    previous = await repo.watermark()
    since = (previous - OVERLAP) if previous else (started - FIRST_PASS_LOOKBACK)
    try:
        result = await db.execute_system(
            "SELECT query_id, event_time, database_name, sql_text FROM NOVA_SYSTEM.AUDIT_LOG "
            "WHERE event_type = 'query' AND status = 'SUCCESS' AND event_time >= %s "
            "AND sql_text IS NOT NULL "
            "AND (LOWER(sql_text) LIKE '%%insert%%' OR LOWER(sql_text) LIKE '%%create%%') "
            "ORDER BY event_time LIMIT %s",
            [since, MAX_ROWS],
        )
        rows = result.get("rows") or []
        found: dict[str, dict] = {}
        for query_id, event_time, database, sql_text in rows:
            lineage = statement_lineage(str(sql_text), database=database or None)
            if lineage is None:
                continue
            for edge in _edges(lineage, query_id, event_time or started):
                current = found.get(edge["id"])
                found[edge["id"]] = (
                    {**edge, "first_seen_at": current["first_seen_at"]} if current else edge
                )
        stored = await repo.existing(list(found))
        for key, edge in found.items():
            earlier = stored.get(key)
            if earlier and earlier.get("first_seen_at"):
                edge["first_seen_at"] = min(earlier["first_seen_at"], edge["first_seen_at"])
        await repo.upsert(list(found.values()))
        watermark = rows[-1][1] if len(rows) >= MAX_ROWS and rows[-1][1] else started
        run.update(
            status="ok",
            finished_at=nova_now(),
            counts={
                "statements": len(rows),
                "edges": len(found),
                "watermark": watermark.isoformat(),
            },
        )
    except Exception as exc:  # noqa: BLE001 - recorded on the run; next pass retries
        log.warning("Lineage harvest failed: %s", redact_error(exc))
        run.update(status="error", finished_at=nova_now(), error=redact_error(exc), counts={})
    await catalog_repo.write_sync_run(run)
    return run
