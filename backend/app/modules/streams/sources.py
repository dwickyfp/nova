"""Stream sources: registration, the capture journal, and capture health.

A *source* is one internal Primary Key table that has at least one Stream. Nova
captures every write to it that goes through Nova (``capture.py``): the source
change and its journal rows are committed in **one** engine transaction by the
dedicated stream writer account. That requires the journal to live in the
source's own database (the engine refuses cross-database transactions), so each
source has two Nova-managed tables beside it:

* ``__nova_stream_changes_<id>`` — one row per changed row of each commit, with
  typed before/after images, the row lineage and an integrity digest;
* ``__nova_stream_lineage_<id>`` — the current lineage of each primary key.

Completeness is checked against the engine, not trusted: every committed write
to a table moves its partition ``VisibleVersion`` by one (measured on the
pinned 4.1.4 engine; see docs/reports/stream-capture-feasibility.md). Nova
records the version before and after each captured commit, so a write that
reached the table any other way (a direct MySQL client, Stream Load, a job)
shows up as an unexplained version and the source becomes ``BLOCKED``: its
Streams fail with an error instead of silently missing a change. A dropped or
recreated table changes its table id and becomes ``SOURCE_INVALID``.

v1 admits unpartitioned Primary Key tables with supported column types and no
Ranger row-filter or masking policy. Anything else is refused with a typed
error rather than captured partially.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any

from app.core.config import settings
from app.modules.streams.codec import Column, quote_identifier, supported
from app.modules.streams.schemas import StreamError

logger = logging.getLogger(__name__)

SOURCES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STREAM_SOURCES (
    source_id VARCHAR(64) NOT NULL,
    epoch BIGINT NOT NULL,
    database_name VARCHAR(128) NOT NULL,
    table_name VARCHAR(128) NOT NULL,
    table_id BIGINT NOT NULL,
    key_columns STRING NOT NULL,
    columns_json STRING NOT NULL,
    schema_fingerprint VARCHAR(64) NOT NULL,
    journal_table VARCHAR(128) NOT NULL,
    lineage_table VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    status_reason VARCHAR(512),
    registered_by VARCHAR(128) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(source_id)
DISTRIBUTED BY HASH(source_id) BUCKETS 1
PROPERTIES ('replication_num'='1')
"""

SOURCE_COMMITS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT_STREAM_SOURCE_COMMITS (
    source_id VARCHAR(64) NOT NULL,
    commit_seq BIGINT NOT NULL,
    commit_id VARCHAR(64) NOT NULL,
    version_before BIGINT NOT NULL,
    version_after BIGINT NOT NULL,
    row_count BIGINT NOT NULL,
    principal VARCHAR(256) NOT NULL,
    active_role VARCHAR(128),
    recorded_at DATETIME NOT NULL
) PRIMARY KEY(source_id, commit_seq)
DISTRIBUTED BY HASH(source_id) BUCKETS 1
PROPERTIES ('replication_num'='1')
"""

DEFINITIONS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STREAM_DEFINITIONS (
    stream_id VARCHAR(64) NOT NULL,
    mode VARCHAR(16) NOT NULL,
    source_id VARCHAR(64) NOT NULL,
    created_by VARCHAR(128) NOT NULL,
    created_at DATETIME NOT NULL,
    show_initial_rows BOOLEAN,
    initial_commit_seq BIGINT
) PRIMARY KEY(stream_id)
DISTRIBUTED BY HASH(stream_id) BUCKETS 1
PROPERTIES ('replication_num'='1')
"""

STREAM_SOURCE_DDL = (SOURCES_DDL, SOURCE_COMMITS_DDL, DEFINITIONS_DDL)

_JOURNAL_DDL = """
CREATE TABLE IF NOT EXISTS {db}.{table} (
    commit_seq BIGINT NOT NULL,
    ordinal INT NOT NULL,
    commit_id VARCHAR(64) NOT NULL,
    epoch BIGINT NOT NULL,
    row_id VARCHAR(64) NOT NULL,
    op VARCHAR(8) NOT NULL,
    key_hash VARCHAR(64) NOT NULL,
    before_image STRING,
    after_image STRING,
    version_before BIGINT NOT NULL,
    principal VARCHAR(256) NOT NULL,
    committed_at DATETIME NOT NULL,
    digest VARCHAR(64) NOT NULL
) DUPLICATE KEY(commit_seq, ordinal)
DISTRIBUTED BY HASH(commit_seq) BUCKETS 1
PROPERTIES ('replication_num'='1')
"""

_LINEAGE_DDL = """
CREATE TABLE IF NOT EXISTS {db}.{table} (
    key_hash VARCHAR(64) NOT NULL,
    row_id VARCHAR(64) NOT NULL,
    alive BOOLEAN NOT NULL,
    commit_seq BIGINT NOT NULL
) PRIMARY KEY(key_hash)
DISTRIBUTED BY HASH(key_hash) BUCKETS 1
PROPERTIES ('replication_num'='1')
"""

#: Status values. Only ``READY`` sources are captured and readable.
REGISTERING, READY, BLOCKED, SOURCE_INVALID, DROPPED = (
    "REGISTERING",
    "READY",
    "BLOCKED",
    "SOURCE_INVALID",
    "DROPPED",
)
_LOCK_PREFIX = "nova:stream:source-lock:"
_LOCK_TTL_MS = 120_000


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    epoch: int
    database: str
    table: str
    table_id: int
    keys: tuple[str, ...]
    columns: tuple[Column, ...]
    schema_fingerprint: str
    journal_table: str
    lineage_table: str
    status: str
    status_reason: str | None = None

    @property
    def qualified(self) -> str:
        return f"{quote_identifier(self.database)}.{quote_identifier(self.table)}"

    @property
    def journal(self) -> str:
        return f"{quote_identifier(self.database)}.{quote_identifier(self.journal_table)}"

    @property
    def lineage(self) -> str:
        return f"{quote_identifier(self.database)}.{quote_identifier(self.lineage_table)}"


@dataclass(frozen=True, slots=True)
class SourceHead:
    """The last captured commit of a source, verified against the engine."""

    sequence: int
    version: int


_SOURCE_COLUMNS = (
    "source_id, epoch, database_name, table_name, table_id, key_columns, columns_json, "
    "schema_fingerprint, journal_table, lineage_table, status, status_reason"
)


def _decode(row: list[Any]) -> SourceRecord:
    columns = tuple(Column(item["name"], item["type"]) for item in json.loads(row[6]))
    return SourceRecord(
        str(row[0]),
        int(row[1]),
        str(row[2]),
        str(row[3]),
        int(row[4]),
        tuple(json.loads(row[5])),
        columns,
        str(row[7]),
        str(row[8]),
        str(row[9]),
        str(row[10]),
        row[11],
    )


def schema_fingerprint(columns: tuple[Column, ...], keys: tuple[str, ...]) -> str:
    payload = json.dumps(
        {"columns": [[c.name, c.sql_type.lower()] for c in columns], "keys": list(keys)},
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def journal_digest(fields: list[Any]) -> str:
    """Integrity of one journal row: only Nova's key can produce it."""
    key = hashlib.sha256(("nova-stream-journal:" + (settings.SECRET_KEY or "")).encode()).digest()
    body = json.dumps(fields, separators=(",", ":"), default=str).encode()
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def writer_configured() -> bool:
    return bool(settings.STREAM_WRITER_USER and settings.STREAM_WRITER_PASSWORD)


@asynccontextmanager
async def writer_conn() -> AsyncIterator[Any]:
    """A connection as the stream writer account, with its role active.

    The writer is the only account that commits captured writes; Ranger grants
    it each registered source and its journal, nothing else.
    """
    if not writer_configured():
        raise StreamError(
            "STREAM_CHANGE_TRACKING_UNAVAILABLE",
            "Streams need the stream writer account (STREAM_WRITER_USER), which is not set "
            "up on this deployment",
        )
    from app.core.database import db

    try:
        connection = db.user_conn(settings.STREAM_WRITER_USER, settings.STREAM_WRITER_PASSWORD)
        conn = await connection.__aenter__()
    except Exception:
        raise StreamError(
            "STREAM_CHANGE_TRACKING_UNAVAILABLE", "The stream writer account could not connect"
        ) from None
    try:
        if settings.STREAM_WRITER_ROLE:
            from app.common.identifiers import check_identifier

            role = check_identifier(settings.STREAM_WRITER_ROLE, field="stream writer role")
            async with conn.cursor() as cur:
                await cur.execute(f"SET ROLE {role}")
        yield conn
    finally:
        await connection.__aexit__(None, None, None)


async def writer_query(conn: Any, sql: str, params: tuple | list | None = None) -> list[tuple]:
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        return list(await cur.fetchall()) if cur.description else []


class SourceRegistry:
    """``CONFIG_STREAM_SOURCES`` and the commit index, through the system connection.

    Everything here is Nova's own control-plane metadata (table ids, versions,
    sequence numbers). Row images are only ever read through ``writer_conn``.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], SourceRecord] = {}
        self._cache_at = 0.0
        self._cache_lock = asyncio.Lock()

    async def _execute(self, sql: str, params: tuple | list | None = None) -> dict:
        from app.core.database import db

        return await db.execute_system(sql, params)

    # ── lookups ─────────────────────────────────────────────────────────

    async def get(self, source_id: str) -> SourceRecord | None:
        result = await self._execute(
            f"SELECT {_SOURCE_COLUMNS} FROM NOVA_SYSTEM.CONFIG_STREAM_SOURCES WHERE source_id=%s",
            (source_id,),
        )
        return _decode(result["rows"][0]) if result["rows"] else None

    async def active_for_table(self, database: str, table: str) -> SourceRecord | None:
        result = await self._execute(
            f"SELECT {_SOURCE_COLUMNS} FROM NOVA_SYSTEM.CONFIG_STREAM_SOURCES "
            "WHERE database_name=%s AND table_name=%s AND status <> 'DROPPED' "
            "ORDER BY epoch DESC LIMIT 1",
            (database, table),
        )
        return _decode(result["rows"][0]) if result["rows"] else None

    async def captured_tables(self) -> dict[tuple[str, str], SourceRecord]:
        """Tables Nova must route writes for, cached for a few seconds per process."""
        now = time.monotonic()
        if now - self._cache_at < settings.STREAM_REGISTRY_CACHE_SECONDS:
            return self._cache
        async with self._cache_lock:
            if time.monotonic() - self._cache_at < settings.STREAM_REGISTRY_CACHE_SECONDS:
                return self._cache
            try:
                result = await self._execute(
                    f"SELECT {_SOURCE_COLUMNS} FROM NOVA_SYSTEM.CONFIG_STREAM_SOURCES "
                    "WHERE status IN ('REGISTERING', 'READY')"
                )
                records = [_decode(row) for row in result["rows"]]
            except Exception:  # noqa: BLE001 - before bootstrap there is nothing to route
                logger.debug("stream source registry unavailable", exc_info=True)
                records = []
            self._cache = {
                (record.database.lower(), record.table.lower()): record for record in records
            }
            self._cache_at = time.monotonic()
        return self._cache

    def forget_cache(self) -> None:
        self._cache_at = 0.0

    async def set_status(self, record: SourceRecord, status: str, reason: str | None) -> None:
        await self._execute(
            "UPDATE NOVA_SYSTEM.CONFIG_STREAM_SOURCES SET status=%s, status_reason=%s, "
            "updated_at=NOW() WHERE source_id=%s",
            (status, (reason or "")[:500] or None, record.source_id),
        )
        self.forget_cache()
        if status in {BLOCKED, SOURCE_INVALID}:
            from app.observability.metrics import STREAM_SOURCE_BLOCKS

            STREAM_SOURCE_BLOCKS.labels(status).inc()
            logger.warning("stream source %s is %s: %s", record.qualified, status, reason)
            with contextlib.suppress(Exception):
                from app.common.audit import write_audit_log

                await write_audit_log(
                    event_type="stream",
                    user_name="nova",
                    action="SOURCE_" + status,
                    object_type="STREAM_SOURCE",
                    object_name=f"{record.database}.{record.table}",
                    status="ERROR",
                    error_message=reason,
                )

    # ── engine facts ────────────────────────────────────────────────────

    async def engine_identity(self, database: str, table: str) -> tuple[int, str, str] | None:
        """``(table_id, model, primary key text)`` of an internal table, or ``None``."""
        result = await self._execute(
            "SELECT TABLE_ID, TABLE_MODEL, PRIMARY_KEY FROM information_schema.tables_config "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
            (database, table),
        )
        if not result["rows"]:
            return None
        row = result["rows"][0]
        return int(row[0]), str(row[1] or ""), str(row[2] or "")

    async def partition_versions(self, database: str, table: str) -> list[int]:
        result = await self._execute(
            f"SHOW PARTITIONS FROM {quote_identifier(database)}.{quote_identifier(table)}"
        )
        index = result["columns"].index("VisibleVersion")
        return [int(row[index]) for row in result["rows"]]

    async def version(self, record: SourceRecord) -> int:
        versions = await self.partition_versions(record.database, record.table)
        if len(versions) != 1:
            raise StreamError(
                "STREAM_SOURCE_UNSUPPORTED", "Streams support unpartitioned tables only"
            )
        return versions[0]

    async def columns(self, database: str, table: str) -> tuple[Column, ...]:
        result = await self._execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.columns "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
            (database, table),
        )
        return tuple(Column(str(row[0]), str(row[1])) for row in result["rows"])

    # ── commit index ────────────────────────────────────────────────────

    async def head_commit(self, record: SourceRecord) -> tuple[int, int] | None:
        """``(commit_seq, version_after)`` of the newest recorded commit."""
        result = await self._execute(
            "SELECT commit_seq, version_after FROM NOVA_SYSTEM.AUDIT_STREAM_SOURCE_COMMITS "
            "WHERE source_id=%s ORDER BY commit_seq DESC LIMIT 1",
            (record.source_id,),
        )
        if not result["rows"]:
            return None
        return int(result["rows"][0][0]), int(result["rows"][0][1])

    async def record_commit(
        self,
        record: SourceRecord,
        sequence: int,
        commit_id: str,
        version_before: int,
        version_after: int,
        rows: int,
        principal: str,
        role: str | None,
    ) -> None:
        await self._execute(
            "INSERT INTO NOVA_SYSTEM.AUDIT_STREAM_SOURCE_COMMITS (source_id, commit_seq, "
            "commit_id, version_before, version_after, row_count, principal, active_role, "
            "recorded_at) SELECT %s, %s, %s, %s, %s, %s, %s, %s, NOW() WHERE NOT EXISTS ("
            "SELECT 1 FROM NOVA_SYSTEM.AUDIT_STREAM_SOURCE_COMMITS WHERE source_id=%s AND "
            "commit_seq=%s)",
            (
                record.source_id,
                sequence,
                commit_id,
                version_before,
                version_after,
                rows,
                principal,
                role,
                record.source_id,
                sequence,
            ),
        )
        stored = await self._execute(
            "SELECT commit_id FROM NOVA_SYSTEM.AUDIT_STREAM_SOURCE_COMMITS WHERE source_id=%s "
            "AND commit_seq=%s",
            (record.source_id, sequence),
        )
        if not stored["rows"] or stored["rows"][0][0] != commit_id:
            raise StreamError("STREAM_SOURCE_BLOCKED", "The capture commit index conflicts")

    async def commit_count(self, record: SourceRecord, after: int, through: int) -> int:
        result = await self._execute(
            "SELECT COUNT(*) FROM NOVA_SYSTEM.AUDIT_STREAM_SOURCE_COMMITS WHERE source_id=%s "
            "AND commit_seq > %s AND commit_seq <= %s",
            (record.source_id, after, through),
        )
        return int(result["rows"][0][0])

    # ── health ──────────────────────────────────────────────────────────

    async def verify(self, record: SourceRecord, *, repair_with: Any = None) -> SourceHead:
        """The verified head, or a typed error after marking the source.

        ``repair_with`` (a writer connection, under the source lock) lets the
        coordinator record a captured commit whose index row was lost to a
        crash between the engine commit and the index write.
        """
        if record.status == SOURCE_INVALID:
            raise StreamError("STREAM_SOURCE_INVALID", record.status_reason or "Source is invalid")
        if record.status == BLOCKED:
            raise StreamError("STREAM_SOURCE_BLOCKED", record.status_reason or "Source is blocked")
        if record.status != READY:
            raise StreamError("STREAM_SOURCE_NOT_READY", "The stream source is not ready yet")
        identity = await self.engine_identity(record.database, record.table)
        if identity is None or identity[0] != record.table_id:
            reason = "The source table was dropped or recreated; recreate the stream"
            await self.set_status(record, SOURCE_INVALID, reason)
            raise StreamError("STREAM_SOURCE_INVALID", reason)
        if await self.columns(record.database, record.table) != record.columns:
            reason = "The source table's columns changed; recreate the stream"
            await self.set_status(record, SOURCE_INVALID, reason)
            raise StreamError("STREAM_SCHEMA_REVIEW_REQUIRED", reason)
        try:
            version = await self.version(record)
        except StreamError as exc:
            await self.set_status(record, SOURCE_INVALID, str(exc))
            raise
        head = await self.head_commit(record)
        if head is None:
            raise StreamError("STREAM_SOURCE_NOT_READY", "The stream source has no baseline")
        sequence, recorded = head
        if version == recorded:
            return SourceHead(sequence, version)
        if repair_with is not None and version == recorded + 1:
            repaired = await self._repair(record, repair_with, sequence, recorded)
            if repaired is not None:
                return repaired
        reason = (
            "A write reached the source table without Nova (engine version "
            f"{version}, captured {recorded}); recreate the stream"
            if version > recorded
            else f"The source table was truncated or rebuilt (engine version {version}); "
            "recreate the stream"
        )
        await self.set_status(record, BLOCKED, reason)
        raise StreamError("STREAM_SOURCE_BLOCKED", reason)

    async def _repair(
        self, record: SourceRecord, conn: Any, sequence: int, version_after: int
    ) -> SourceHead | None:
        rows = await writer_query(
            conn,
            f"SELECT commit_id, version_before, principal, COUNT(*) FROM {record.journal} "
            "WHERE commit_seq=%s GROUP BY commit_id, version_before, principal",
            (sequence + 1,),
        )
        if len(rows) != 1 or int(rows[0][1]) != version_after:
            return None
        await self.record_commit(
            record,
            sequence + 1,
            str(rows[0][0]),
            version_after,
            version_after + 1,
            int(rows[0][3]),
            str(rows[0][2]),
            None,
        )
        logger.info("repaired capture index of %s at commit %s", record.qualified, sequence + 1)
        return SourceHead(sequence + 1, version_after + 1)

    # ── registration ────────────────────────────────────────────────────

    async def register(self, database: str, table: str, principal: str) -> SourceRecord:
        """Admit a table as a stream source, or return its current registration."""
        identity = await self.engine_identity(database, table)
        if identity is None:
            raise StreamError("STREAM_SOURCE_NOT_FOUND", f"Table {database}.{table} does not exist")
        table_id, model, primary_key = identity
        current = await self.active_for_table(database, table)
        if (
            current is not None
            and current.table_id == table_id
            and current.status
            in {
                READY,
                REGISTERING,
            }
        ):
            if current.status == REGISTERING:
                raise StreamError("STREAM_SOURCE_NOT_READY", "The source is being registered")
            return current
        if model.upper() not in {"PRIMARY_KEYS", "PRIMARY_KEY"}:
            raise StreamError(
                "STREAM_MODE_UNSUPPORTED_BY_SOURCE",
                "Streams need a Primary Key table: row lineage and before images come from "
                "the primary key",
            )
        keys = tuple(part.strip().strip("`") for part in primary_key.split(",") if part.strip())
        columns = await self.columns(database, table)
        if not keys or any(key not in {c.name for c in columns} for key in keys):
            raise StreamError("STREAM_SOURCE_UNSUPPORTED", "The table's primary key is unknown")
        unsupported = [c.name for c in columns if not supported(c.sql_type)]
        if unsupported:
            raise StreamError(
                "STREAM_SCHEMA_UNSUPPORTED",
                f"Column types of {', '.join(unsupported)} cannot be captured exactly",
            )
        if len(await self.partition_versions(database, table)) != 1:
            raise StreamError(
                "STREAM_SOURCE_UNSUPPORTED",
                "Streams support unpartitioned tables only: a partitioned table cannot prove "
                "that every write was captured",
            )
        await require_governance_supported(database, table)
        epoch = (current.epoch + 1) if current is not None else 1
        if current is not None:
            await self.set_status(current, SOURCE_INVALID, "The source table was recreated")
        source_id = uuid.uuid4().hex
        record = SourceRecord(
            source_id,
            epoch,
            database,
            table,
            table_id,
            keys,
            columns,
            schema_fingerprint(columns, keys),
            f"__nova_stream_changes_{source_id[:16]}",
            f"__nova_stream_lineage_{source_id[:16]}",
            REGISTERING,
        )
        await self._execute(
            _JOURNAL_DDL.format(
                db=quote_identifier(database), table=quote_identifier(record.journal_table)
            )
        )
        await self._execute(
            _LINEAGE_DDL.format(
                db=quote_identifier(database), table=quote_identifier(record.lineage_table)
            )
        )
        await grant_writer(record)
        await self._execute(
            "INSERT INTO NOVA_SYSTEM.CONFIG_STREAM_SOURCES (source_id, epoch, database_name, "
            "table_name, table_id, key_columns, columns_json, schema_fingerprint, journal_table, "
            "lineage_table, status, status_reason, registered_by, created_at, updated_at) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, NOW(), NOW())",
            (
                record.source_id,
                epoch,
                database,
                table,
                table_id,
                json.dumps(list(keys)),
                json.dumps([{"name": c.name, "type": c.sql_type} for c in columns]),
                record.schema_fingerprint,
                record.journal_table,
                record.lineage_table,
                REGISTERING,
                principal,
            ),
        )
        self.forget_cache()
        # Every Nova process must route writes to this table through the
        # coordinator before the baseline is taken: wait out their caches.
        await asyncio.sleep(settings.STREAM_REGISTRY_CACHE_SECONDS + 1.0)
        async with source_lock(record.source_id):
            version = await self.version(record)
            await self.record_commit(record, 0, "baseline", version, version, 0, principal, None)
            await self._execute(
                "UPDATE NOVA_SYSTEM.CONFIG_STREAM_SOURCES SET status='READY', updated_at=NOW() "
                "WHERE source_id=%s",
                (record.source_id,),
            )
        self.forget_cache()
        return replace(record, status=READY)

    async def release_if_unused(self, record: SourceRecord) -> None:
        """Stop capturing a source once its last Stream is dropped."""
        result = await self._execute(
            "SELECT s.stream_id, s.status FROM NOVA_SYSTEM.CONFIG_STREAMS s "
            "JOIN (SELECT catalog_name, database_name, schema_name, name, MAX(generation) g "
            "FROM NOVA_SYSTEM.CONFIG_STREAMS GROUP BY catalog_name, database_name, schema_name, "
            "name) m ON s.catalog_name=m.catalog_name AND s.database_name=m.database_name AND "
            "s.schema_name=m.schema_name AND s.name=m.name AND s.generation=m.g "
            "WHERE s.source_id=%s AND s.status='READY'",
            (record.source_id,),
        )
        if not result["rows"]:
            await self.set_status(record, DROPPED, "No stream reads this source any more")


async def require_governance_supported(database: str, table: str) -> None:
    """Refuse sources with Ranger row filters or masks.

    A stream returns historical before images (deleted and overwritten rows);
    a filter or mask evaluated on current rows cannot be shown to govern them.
    """
    if not settings.RANGER_PASSWORD:
        return
    from app.integrations.ranger.client import ranger_client
    from app.modules.access_control.service import AccessControlService

    try:
        policies = await ranger_client.list_policies()
    except Exception as exc:  # noqa: BLE001 - unknown governance is not "none"
        raise StreamError(
            "STREAM_GOVERNANCE_UNSUPPORTED", "Data policies could not be read for the source"
        ) from exc
    for policy in policies:
        if (
            policy.get("service", settings.RANGER_SERVICE_NAME) == settings.RANGER_SERVICE_NAME
            and policy.get("isEnabled", True)
            and int(policy.get("policyType", 0)) in {1, 2}
            and AccessControlService._resource_matches(policy, "default_catalog", database, table)
        ):
            raise StreamError(
                "STREAM_GOVERNANCE_UNSUPPORTED",
                "The source has a row filter or masking policy; a stream would expose "
                "historical values those policies cannot govern",
            )


async def grant_writer(record: SourceRecord) -> None:
    """Give the writer role this source and its journal, then wait until it works."""
    if not writer_configured():
        raise StreamError(
            "STREAM_CHANGE_TRACKING_UNAVAILABLE",
            "Streams need the stream writer account (STREAM_WRITER_USER), which is not set "
            "up on this deployment",
        )
    if settings.RANGER_PASSWORD and settings.STREAM_WRITER_ROLE:
        from app.integrations.ranger.client import ranger_client
        from app.integrations.ranger.schemas import (
            RangerPolicy,
            RangerPolicyItem,
            RangerPolicyItemAccess,
            RangerPolicyResource,
        )

        policy = RangerPolicy(
            service=settings.RANGER_SERVICE_NAME,
            name=f"nova-managed/stream-writer/{record.source_id}",
            description=f"Stream capture of {record.database}.{record.table}",
            resources={
                "catalog": RangerPolicyResource(values=["default_catalog"]),
                "database": RangerPolicyResource(values=[record.database]),
                "table": RangerPolicyResource(
                    values=[record.table, record.journal_table, record.lineage_table]
                ),
                "column": RangerPolicyResource(values=["*"]),
            },
            policyItems=[
                RangerPolicyItem(
                    roles=[settings.STREAM_WRITER_ROLE],
                    accesses=[
                        RangerPolicyItemAccess(type=access)
                        for access in ("select", "insert", "update", "delete")
                    ],
                )
            ],
        )
        await ranger_client.put_policy(policy)
    deadline = time.monotonic() + settings.STREAM_WRITER_GRANT_WAIT_SECONDS
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            async with writer_conn() as conn:
                for name in (record.qualified, record.journal, record.lineage):
                    await writer_query(conn, f"SELECT 1 FROM {name} LIMIT 0")
                return
        except StreamError:
            raise
        except Exception as exc:  # noqa: BLE001 - policy refresh is eventual
            last = exc
            await asyncio.sleep(2.0)
    raise StreamError(
        "STREAM_CHANGE_TRACKING_UNAVAILABLE",
        "The stream writer account was not granted the source in time",
    ) from last


async def revoke_writer(record: SourceRecord) -> None:
    if not (settings.RANGER_PASSWORD and settings.STREAM_WRITER_ROLE):
        return
    from app.integrations.ranger.client import ranger_client

    with contextlib.suppress(Exception):
        policy = await ranger_client.get_policy(f"nova-managed/stream-writer/{record.source_id}")
        if policy and policy.get("id") is not None:
            await ranger_client.delete_policy(int(policy["id"]))


@asynccontextmanager
async def source_lock(source_id: str) -> AsyncIterator[None]:
    """Serialize captured writes of one source across Nova processes.

    A lease, not the proof: the engine version check after each commit is
    what detects a write that happened outside this lock.
    """
    from app.core.redis import session_store

    redis = session_store._redis
    if redis is None:
        raise StreamError("STREAM_SOURCE_BUSY", "The coordination store is unavailable")
    key, token = _LOCK_PREFIX + source_id, secrets.token_hex(16)
    deadline = time.monotonic() + settings.STREAM_WRITE_LOCK_WAIT_SECONDS
    while not await redis.set(key, token, nx=True, px=_LOCK_TTL_MS):
        if time.monotonic() > deadline:
            raise StreamError(
                "STREAM_SOURCE_BUSY", "Another write to this stream source is in progress"
            )
        await asyncio.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            if (await redis.get(key)) in {token, token.encode()}:
                await redis.delete(key)


source_registry = SourceRegistry()
