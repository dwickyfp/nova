"""Streams: objects, verified reads, SYSTEM$STREAM_HAS_DATA and consumption.

A Stream is a name, a source and an offset; it stores no rows. Its rows for an
interval are the reduced journal events of its source between the stream's
committed offset and the source's verified head (``reducer.py``). Reading
(``SELECT``) never moves the offset; a consuming ``INSERT … SELECT`` moves it
only after the target insert is verified visible (``consumption.py``).

Every read re-checks, at that moment: the stream exists and is READY; the
caller's active role may read the source (Ranger); the source has no row
filter or mask; the source is verified against the engine (no bypassing
write, same table); the interval's commits form one unbroken, digest-checked
sequence. Any doubt is an error, never an empty or partial result.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.modules.streams import codec
from app.modules.streams.claims import ClaimRepository
from app.modules.streams.consumption import (
    Consumption,
    ConsumptionState,
    StreamConsumptionService,
)
from app.modules.streams.journal import StarRocksConsumptionJournal
from app.modules.streams.namespace import StreamName
from app.modules.streams.reducer import (
    ACTION,
    IS_UPDATE,
    ROW_ID,
    ChangeEvent,
    StreamRow,
    reduce_changes,
)
from app.modules.streams.repository import StreamRecord, StreamRepository
from app.modules.streams.schemas import (
    ChangeCursor,
    ChangeSnapshot,
    StreamError,
    StreamMode,
    operation_id,
)
from app.modules.streams.sources import (
    READY,
    SourceHead,
    SourceRecord,
    journal_digest,
    require_governance_supported,
    source_registry,
    writer_conn,
    writer_query,
)
from app.modules.streams.verification import TargetCommitVerifier, TargetIdentity


@dataclass(frozen=True, slots=True)
class StreamCaller:
    username: str
    role: str | None
    security_context_version: int = 1


@dataclass(frozen=True, slots=True)
class ResolvedStream:
    record: StreamRecord
    source: SourceRecord
    mode: StreamMode
    after: int
    head: SourceHead

    @property
    def has_data(self) -> bool:
        return self.head.sequence > self.after


class StreamService:
    def __init__(self) -> None:
        self.repository = StreamRepository()
        self.journal = StarRocksConsumptionJournal()
        self.consumption = StreamConsumptionService(
            ClaimRepository(), self.journal, TargetCommitVerifier()
        )

    async def _execute(self, sql: str, params: tuple | list | None = None) -> dict:
        from app.core.database import db

        return await db.execute_system(sql, params)

    # ── authorization ───────────────────────────────────────────────────

    async def authorize_read(self, caller: StreamCaller, database: str, table: str) -> None:
        if not caller.role:
            raise StreamError("STREAM_ACCESS_DENIED", "Select an active role to use streams")
        from app.modules.access_control.domain import AccessRequest
        from app.modules.access_control.service import access_control_service

        allowed = await access_control_service.role_allows(
            principal=caller.username,
            active_role=caller.role,
            requests=[AccessRequest("select", "default_catalog", database, table)],
        )
        if not all(allowed):
            # The same answer as a missing stream: existence is not disclosed.
            raise StreamError("STREAM_NOT_FOUND", "Stream does not exist or is not accessible")

    # ── objects ─────────────────────────────────────────────────────────

    async def mode_of(self, stream_id: str) -> StreamMode:
        result = await self._execute(
            "SELECT mode FROM NOVA_SYSTEM.CONFIG_STREAM_DEFINITIONS WHERE stream_id=%s",
            (stream_id,),
        )
        if not result["rows"]:
            raise StreamError("STREAM_METADATA_INCONSISTENT", "The stream has no definition")
        return StreamMode(str(result["rows"][0][0]))

    async def current(self, name: StreamName) -> StreamRecord:
        record = await self.repository.current(name)
        if record is None or record.status != READY:
            raise StreamError("STREAM_NOT_FOUND", "Stream does not exist or is not accessible")
        return record

    async def create(
        self,
        name: StreamName,
        source: StreamName,
        mode: StreamMode,
        caller: StreamCaller,
        *,
        if_not_exists: bool = False,
    ) -> StreamRecord:
        if not caller.role:
            raise StreamError("STREAM_ACCESS_DENIED", "Select an active role to create a stream")
        existing = await self.repository.current(name)
        if existing is not None and existing.status == READY:
            await self.authorize_read(caller, existing.source.database, existing.source.name)
            if (
                if_not_exists
                and existing.source == source
                and await self.mode_of(existing.stream_id) == mode
            ):
                return existing
            raise StreamError(
                "STREAM_ALREADY_EXISTS",
                "Stream already exists"
                + (" with a different source or mode" if if_not_exists else ""),
            )
        if await self._table_exists(name.database, name.name):
            raise StreamError("STREAM_NAME_COLLISION", "A table or view occupies this name")
        await self.authorize_read(caller, source.database, source.name)
        source_record = await source_registry.register(
            source.database, source.name, caller.username
        )
        head = await source_registry.verify(source_record)
        stream_id = uuid.uuid4().hex
        record = StreamRecord(
            name,
            existing.generation + 1 if existing else 0,
            operation_id(stream_id),
            source,
            source_record.source_id,
            caller.role,
            ChangeCursor(source_record.epoch, head.sequence),
        )
        await self._execute(
            "INSERT INTO NOVA_SYSTEM.CONFIG_STREAM_DEFINITIONS (stream_id, mode, source_id, "
            "created_by, created_at) VALUES (%s, %s, %s, %s, NOW())",
            (record.stream_id, str(mode), source_record.source_id, caller.username),
        )
        return await self.repository.publish(record)

    async def drop(self, name: StreamName, caller: StreamCaller, *, if_exists: bool = False):
        record = await self.repository.current(name)
        if record is None or record.status != READY:
            if if_exists:
                return None
            raise StreamError("STREAM_NOT_FOUND", "Stream does not exist or is not accessible")
        await self.authorize_read(caller, record.source.database, record.source.name)
        if caller.role != record.owner_role:
            raise StreamError(
                "STREAM_ACCESS_DENIED",
                f"Stream is owned by role {record.owner_role}; activate it to drop the stream",
            )
        from dataclasses import replace

        dropped = await self.repository.publish(
            replace(record, generation=record.generation + 1, status="DROPPED")
        )
        source = await source_registry.get(record.source_id)
        if source is not None:
            await source_registry.release_if_unused(source)
        return dropped

    async def _table_exists(self, database: str, table: str) -> bool:
        result = await self._execute(
            "SELECT 1 FROM information_schema.tables WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
            (database, table),
        )
        return bool(result["rows"])

    async def list_streams(self, database: str, caller: StreamCaller) -> list[dict[str, Any]]:
        result = await self._execute(
            "SELECT s.name, s.source_database, s.source_name, s.stream_id, s.owner_role, "
            "s.created_at, d.mode FROM NOVA_SYSTEM.CONFIG_STREAMS s JOIN (SELECT catalog_name, "
            "database_name, schema_name, name, MAX(generation) g FROM NOVA_SYSTEM.CONFIG_STREAMS "
            "WHERE database_name=%s GROUP BY catalog_name, database_name, schema_name, name) m "
            "ON s.catalog_name=m.catalog_name AND s.database_name=m.database_name AND "
            "s.schema_name=m.schema_name AND s.name=m.name AND s.generation=m.g "
            "LEFT JOIN NOVA_SYSTEM.CONFIG_STREAM_DEFINITIONS d ON d.stream_id=s.stream_id "
            "WHERE s.status='READY' ORDER BY s.name",
            (database,),
        )
        visible = []
        for row in result["rows"]:
            try:
                await self.authorize_read(caller, str(row[1]), str(row[2]))
            except StreamError:
                continue
            visible.append(
                {
                    "name": row[0],
                    "database": database,
                    "source": f"{row[1]}.{row[2]}",
                    "stream_id": row[3],
                    "owner_role": row[4],
                    "created_at": row[5],
                    "mode": row[6] or "STANDARD",
                }
            )
        return visible

    # ── reads ───────────────────────────────────────────────────────────

    async def resolve(self, name: StreamName, caller: StreamCaller) -> ResolvedStream:
        record = await self.current(name)
        await self.authorize_read(caller, record.source.database, record.source.name)
        source = await source_registry.get(record.source_id)
        if source is None or source.epoch != record.cursor.epoch:
            raise StreamError(
                "STREAM_SOURCE_INVALID", "The stream's source was replaced; recreate the stream"
            )
        await require_governance_supported(source.database, source.table)
        head = await source_registry.verify(source)
        after = await self.journal.committed_cursor(record.stream_id, record.cursor)
        if after.sequence > head.sequence:
            raise StreamError(
                "STREAM_CONSUMPTION_BLOCKED", "The stream offset is ahead of its source"
            )
        return ResolvedStream(
            record, source, await self.mode_of(record.stream_id), after.sequence, head
        )

    async def trigger_state(self, stream_id: str, name: StreamName) -> tuple[bool, list[int]]:
        """Pending changes of a task's stream, from control-plane facts only.

        ``(has_data, [offset, head])``. The stream must still be the one the
        task was bound to; a stream recreated under the same name is not.
        Authorization of the run's identity happens when the run evaluates
        its ``WHEN``; this only decides whether a run is due.
        """
        record = await self.current(name)
        if record.stream_id != stream_id:
            raise StreamError(
                "STREAM_DEPENDENCY_INVALID",
                f"Stream {name.database}.{name.name} was recreated; recreate the task",
            )
        source = await source_registry.get(record.source_id)
        if source is None or source.epoch != record.cursor.epoch:
            raise StreamError("STREAM_SOURCE_INVALID", "The stream's source was replaced")
        head = await source_registry.verify(source)
        after = await self.journal.committed_cursor(record.stream_id, record.cursor)
        return head.sequence > after.sequence, [after.sequence, head.sequence]

    async def has_data(self, name: StreamName, caller: StreamCaller) -> bool:
        """Snowflake's conservative answer: TRUE when commits are pending.

        Never FALSE on doubt: every check in ``resolve`` raises instead.
        """
        return (await self.resolve(name, caller)).has_data

    async def rows(self, resolved: ResolvedStream) -> list[StreamRow]:
        if not resolved.has_data:
            return []
        source, after, through = resolved.source, resolved.after, resolved.head.sequence
        index = await self._execute(
            "SELECT commit_seq, commit_id, row_count FROM NOVA_SYSTEM.AUDIT_STREAM_SOURCE_COMMITS "
            "WHERE source_id=%s AND commit_seq > %s AND commit_seq <= %s ORDER BY commit_seq",
            (source.source_id, after, through),
        )
        commits = {int(row[0]): (str(row[1]), int(row[2])) for row in index["rows"]}
        if sorted(commits) != list(range(after + 1, through + 1)):
            raise StreamError("STREAM_SOURCE_BLOCKED", "The source's commit sequence has a gap")
        total = sum(count for _id, count in commits.values())
        if total > settings.STREAM_READ_MAX_ROWS:
            raise StreamError(
                "STREAM_READ_TOO_LARGE",
                f"The stream holds {total:,} changes; one read takes at most "
                f"{settings.STREAM_READ_MAX_ROWS:,}. Consume it in a task more often",
            )
        async with writer_conn() as writer:
            raw = await writer_query(
                writer,
                "SELECT commit_seq, ordinal, commit_id, epoch, row_id, op, key_hash, "
                f"before_image, after_image, version_before, digest FROM {source.journal} "
                "WHERE commit_seq > %s AND commit_seq <= %s",
                (after, through),
            )
        seen: dict[int, int] = {}
        events: list[ChangeEvent] = []
        for row in raw:
            (
                seq,
                ordinal,
                commit_id,
                epoch,
                row_id,
                op,
                key,
                before,
                after_image,
                version_before,
                digest,
            ) = row
            expected = journal_digest(
                [
                    int(seq),
                    int(ordinal),
                    commit_id,
                    int(epoch),
                    row_id,
                    op,
                    key,
                    before,
                    after_image,
                    int(version_before),
                ]
            )
            if (
                digest != expected
                or commits.get(int(seq), ("",))[0] != commit_id
                or int(epoch) != source.epoch
            ):
                await source_registry.set_status(
                    source, "BLOCKED", "A journal row failed its integrity check"
                )
                raise StreamError(
                    "STREAM_SOURCE_BLOCKED", "A journal row failed its integrity check"
                )
            seen[int(seq)] = seen.get(int(seq), 0) + 1
            events.append(
                ChangeEvent(
                    int(seq),
                    int(ordinal),
                    str(row_id),
                    op,
                    codec.decode_image(source.columns, before),
                    codec.decode_image(source.columns, after_image),
                )
            )
        if any(seen.get(seq, 0) != count for seq, (_id, count) in commits.items()):
            raise StreamError("STREAM_SOURCE_BLOCKED", "The journal is missing captured rows")
        return reduce_changes(events, resolved.mode)

    def relation_sql(self, resolved: ResolvedStream, rows: list[StreamRow]) -> str:
        """A typed derived table for the rows, unioned with a zero-row read of the
        source so the engine checks the caller's SELECT privilege in the same
        statement.

        Names are select-list aliases: the engine lower-cases the column list of
        a derived table, and Snowflake's ``METADATA$ACTION`` keeps its case.
        """
        columns = resolved.source.columns
        out = [codec.quote_identifier(c.name) for c in columns] + [
            codec.quote_identifier(name) for name in (ACTION, IS_UPDATE, ROW_ID)
        ]
        guard_values = [codec.quote_identifier(c.name) for c in columns] + [
            "CAST(NULL AS VARCHAR)", "CAST(NULL AS BOOLEAN)", "CAST(NULL AS VARCHAR)",
        ]
        guard = (
            "SELECT "
            + ", ".join(f"{value} AS {name}" for value, name in zip(guard_values, out, strict=True))
            + f" FROM {resolved.source.qualified} WHERE 1 = 0"
        )
        if not rows:
            return f"({guard})"
        inner = [f"__c{index}" for index in range(len(out))]
        values = ", ".join(
            "("
            + ", ".join(
                [codec.literal(c, row.values[c.name]) for c in columns]
                + [
                    codec.string_literal(row.action),
                    "TRUE" if row.is_update else "FALSE",
                    codec.string_literal(row.row_id),
                ]
            )
            + ")"
            for row in rows
        )
        rows_select = (
            "SELECT "
            + ", ".join(f"{alias} AS {name}" for alias, name in zip(inner, out, strict=True))
            + f" FROM (VALUES {values}) AS __nova_stream({', '.join(inner)})"
        )
        return f"({rows_select} UNION ALL {guard})"

    async def status(self, name: StreamName, caller: StreamCaller) -> dict[str, Any]:
        record = await self.current(name)
        await self.authorize_read(caller, record.source.database, record.source.name)
        source = await source_registry.get(record.source_id)
        mode = await self.mode_of(record.stream_id)
        out: dict[str, Any] = {
            "name": f"{name.database}.{name.name}",
            "source": f"{record.source.database}.{record.source.name}",
            "mode": str(mode),
            "owner_role": record.owner_role,
            "stream_id": record.stream_id,
        }
        try:
            resolved = await self.resolve(name, caller)
            out.update(
                status="READY",
                offset=resolved.after,
                head=resolved.head.sequence,
                pending_commits=resolved.head.sequence - resolved.after,
                has_data=resolved.has_data,
            )
        except StreamError as exc:
            out.update(status=exc.code.removeprefix("STREAM_"), reason=str(exc))
        if source is not None:
            out["source_status"] = source.status
        return out

    async def backlog(self, name: StreamName, caller: StreamCaller) -> dict[str, Any]:
        resolved = await self.resolve(name, caller)
        result = await self._execute(
            "SELECT COUNT(*), COALESCE(SUM(row_count), 0), MIN(recorded_at) FROM "
            "NOVA_SYSTEM.AUDIT_STREAM_SOURCE_COMMITS WHERE source_id=%s AND commit_seq > %s "
            "AND commit_seq <= %s",
            (resolved.source.source_id, resolved.after, resolved.head.sequence),
        )
        commits, changes, oldest = result["rows"][0]
        return {
            "name": f"{name.database}.{name.name}",
            "pending_commits": int(commits),
            "pending_row_changes": int(changes),
            "oldest_pending_commit_at": oldest,
            "estimate": "row changes before reduction; a Standard stream may show fewer",
        }

    # ── consumption ─────────────────────────────────────────────────────

    async def consume(
        self,
        resolved: ResolvedStream,
        caller: StreamCaller,
        target_database: str,
        target_table: str,
        statement_digest: str,
        submit: Any,
        *,
        engine_principal: str | None = None,
    ) -> ConsumptionState:
        """Advance the offset to the pinned head after ``submit(label)`` is verified.

        ``submit`` runs the consuming INSERT with the given label on the
        caller's session. One winner per start offset; a retry after a
        verified failure takes the next slot; anything unverifiable blocks.
        """
        record = resolved.record
        attempt = await self.journal.failed_attempts(
            record.stream_id, record.generation, resolved.after
        )
        snapshot = ChangeSnapshot(
            resolved.source.source_id,
            ChangeCursor(record.cursor.epoch, resolved.after),
            ChangeCursor(record.cursor.epoch, resolved.head.sequence),
            1,
        )
        consume_id = operation_id(
            "consume",
            record.stream_id,
            record.generation,
            resolved.after,
            resolved.head.sequence,
            attempt,
            target_database,
            target_table,
            statement_digest,
            caller.username,
            caller.role or "",
        )
        label = "nova_stream_" + consume_id[:48]
        consume = Consumption(
            consume_id,
            record.stream_id,
            record.generation,
            snapshot,
            TargetIdentity(
                target_database, target_table, label, engine_principal or caller.username
            ),
            statement_digest,
            caller.role,
            caller.security_context_version,
            attempt,
        )
        try:
            state = await self.consumption.dispatch(
                consume,
                attempt_id=operation_id(uuid.uuid4().hex),
                submit=lambda value: submit(value.target.label),
            )
        except StreamError as exc:
            if exc.code == "STREAM_CONSUMPTION_BLOCKED":
                raise StreamError(
                    "STREAM_CONSUMPTION_BLOCKED",
                    "Another consumer is consuming this stream, or an earlier consumption "
                    "needs verification; retry shortly",
                ) from None
            raise
        if state == ConsumptionState.OFFSET_COMMITTED:
            await self._collect(resolved)
        return state

    async def _collect(self, resolved: ResolvedStream) -> None:
        """Remove journal rows every stream of the source has consumed."""
        floor = None
        result = await self._execute(
            "SELECT s.stream_id, s.cursor_epoch, s.cursor_sequence "
            "FROM NOVA_SYSTEM.CONFIG_STREAMS s JOIN (SELECT catalog_name, database_name, "
            "schema_name, name, MAX(generation) g FROM "
            "NOVA_SYSTEM.CONFIG_STREAMS GROUP BY catalog_name, database_name, schema_name, name) m "
            "ON s.catalog_name=m.catalog_name AND s.database_name=m.database_name AND "
            "s.schema_name=m.schema_name AND s.name=m.name AND s.generation=m.g "
            "WHERE s.source_id=%s AND s.status='READY'",
            (resolved.source.source_id,),
        )
        for stream_id, epoch, sequence in result["rows"]:
            try:
                cursor = await self.journal.committed_cursor(
                    str(stream_id), ChangeCursor(int(epoch), int(sequence))
                )
            except StreamError:
                return
            floor = cursor.sequence if floor is None else min(floor, cursor.sequence)
        if floor is None or floor <= 0:
            return
        try:
            async with writer_conn() as writer:
                await writer_query(
                    writer,
                    f"DELETE FROM {resolved.source.journal} WHERE commit_seq <= %s",
                    (floor,),
                )
        except Exception:  # noqa: BLE001 - collection is housekeeping; retried next time
            return


def stream_caller(username: str, role: str | None, version: int = 1) -> StreamCaller:
    return StreamCaller(username, role, version)


stream_service = StreamService()
