"""Table-to-table lineage harvested from ``AUDIT_LOG``, keyed by securable id.

Every successful statement Nova executes leaves a redacted ``AUDIT_LOG`` row
with its SQL text and session database. A pass reads the rows written since the
previous pass, keeps the statements that write one relation from a query
(:func:`app.sql_frontend.analysis.lineage.statement_lineage`), and upserts one
edge per ``(source, target)`` pair into ``nova_meta.catalog_lineage``.

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

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from app.common.metadata_audit import AuditFilter, AuditReader
from app.sql_frontend.analysis.lineage import StatementLineage, statement_lineage

from .metadata_repository import catalog_transaction
from .repository import row_id
from .securables import relation_id
from .sync import nova_now, redact_error

log = logging.getLogger(__name__)

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


class LineageRepository:
    async def edges_of(self, securable_id: str, limit: int = 200) -> list[dict]:
        async with catalog_transaction(read_only=True) as repo:
            return await repo.lineage_edges_of(securable_id, limit)

    async def existing(self, ids: list[str]) -> dict[str, dict]:
        async with catalog_transaction(read_only=True) as repo:
            return await repo.lineage_by_ids(ids)

    async def upsert(self, edges: list[dict]) -> None:
        async with catalog_transaction() as repo:
            await repo.upsert_lineage(edges)

    async def watermark(self) -> datetime | None:
        async with catalog_transaction(read_only=True) as repo:
            return await repo.lineage_watermark()

    async def statements(self, since: datetime) -> list[tuple]:
        async with catalog_transaction(read_only=True) as repo:
            columns = ("query_id", "event_time", "database_name", "sql_text")
            rows = await AuditReader(repo.connection).events(
                AuditFilter(event_type="query", status="SUCCESS", since=since,
                            sql_present=True, sql_contains_any=("insert", "create")),
                columns, limit=MAX_ROWS, newest_first=False,
            )
        return [tuple(row[column] for column in columns) for row in rows]


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
        rows = await repo.statements(since)
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
