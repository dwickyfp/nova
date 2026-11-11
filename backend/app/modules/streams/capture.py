"""Capture writes to a stream source: the source change and its journal, atomically.

When a statement writes a table that has Streams (a *source*), Nova does not
send it to the engine as written. The coordinator:

1. authorizes the write for the caller's active role in Ranger (the same check
   notebook write-back uses), because the commit itself runs as the stream
   writer account;
2. under the source's lock, verifies the source against the engine version
   (a write that bypassed Nova blocks the source instead of being missed);
3. reads the rows the statement affects **as the caller** (so the caller's own
   privileges decide what the statement may read) and computes the exact
   after images with the engine (``CAST`` to each column's type);
4. reads the current images of the affected keys and their lineage as the
   writer;
5. commits, in **one** engine transaction as the writer: the journal rows, the
   lineage rows, and the source change expressed as literal after images (an
   upsert) or literal keys (a delete). Any error rolls the transaction back;
6. checks that the source's engine version moved by exactly one and records
   the commit in Nova's commit index. Anything else blocks the source.

Supported: ``INSERT INTO t [(all columns)] VALUES …`` / ``SELECT …``,
``UPDATE t SET … [WHERE …]`` and ``DELETE FROM t [WHERE …]``. Other writes to a
source (``INSERT OVERWRITE``, ``TRUNCATE``, ``MERGE``, partial column lists,
``UPDATE … FROM``, ``DELETE … USING``, labels, partitions, ``DEFAULT``) are
refused with ``STREAM_SOURCE_WRITE_UNSUPPORTED``: drop the table's streams to
use them.
"""

from __future__ import annotations

import contextlib
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.modules.streams import codec
from app.modules.streams.codec import quote_identifier
from app.modules.streams.schemas import StreamError
from app.modules.streams.sources import (
    BLOCKED,
    READY,
    REGISTERING,
    SourceRecord,
    journal_digest,
    source_lock,
    source_registry,
    writer_conn,
    writer_query,
)

_WRITE_ACCESS = {"insert": "insert", "update": "update", "delete": "delete"}
MAX_STATEMENT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Caller:
    """Who writes, and how to run SQL as them (their own engine session)."""

    username: str
    role: str | None
    query: Callable[[str], Awaitable[tuple[list[str], list[list[Any]]]]]
    run_native: Callable[[], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class CaptureOutcome:
    affected_rows: int
    commit_sequence: int | None
    native: Any = None


@dataclass(frozen=True, slots=True)
class _Change:
    key: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None


def unsupported(reason: str) -> StreamError:
    return StreamError(
        "STREAM_SOURCE_WRITE_UNSUPPORTED",
        f"{reason} on a table with streams is not supported; drop its streams first",
    )


def _slice(sql: str, ctx: Any) -> str:
    return sql[ctx.start.start : ctx.stop.stop + 1]


class CaptureCoordinator:
    async def write(
        self, record: SourceRecord, statement: Any, sql: str, kind: str, caller: Caller
    ) -> CaptureOutcome:
        if kind not in _WRITE_ACCESS:
            raise unsupported({"overwrite": "INSERT OVERWRITE"}.get(kind, kind.upper()))
        await self._authorize(record, kind, caller)
        plan = self._plan(record, statement, sql, kind)
        async with source_lock(record.source_id):
            fresh = await source_registry.get(record.source_id)
            if fresh is None or fresh.status not in {READY, REGISTERING}:
                # No longer captured (blocked, invalid, dropped): an ordinary write.
                return CaptureOutcome(0, None, await caller.run_native())
            if fresh.status == REGISTERING:
                # Before the baseline: write natively, inside the lock, so the
                # baseline cannot be taken between this write and its commit.
                return CaptureOutcome(0, None, await caller.run_native())
            async with writer_conn() as writer:
                try:
                    head = await source_registry.verify(fresh, repair_with=writer)
                except StreamError as exc:
                    if exc.code in {
                        "STREAM_SOURCE_BLOCKED",
                        "STREAM_SOURCE_INVALID",
                        "STREAM_SCHEMA_REVIEW_REQUIRED",
                    }:
                        return CaptureOutcome(0, None, await caller.run_native())
                    raise
                changes, matched = await self._changes(fresh, plan, kind, caller, writer)
                if not changes:
                    return CaptureOutcome(matched, None)
                sequence = head.sequence + 1
                commit_id = uuid.uuid4().hex
                await self._commit(
                    fresh, kind, changes, sequence, commit_id, head.version, caller, writer
                )
            version_after = await source_registry.version(fresh)
            if version_after != head.version + 1:
                await source_registry.set_status(
                    fresh,
                    BLOCKED,
                    f"The engine version moved from {head.version} to {version_after} during "
                    "a captured write: another writer reached the table",
                )
                return CaptureOutcome(matched, sequence)
            await source_registry.record_commit(
                fresh,
                sequence,
                commit_id,
                head.version,
                version_after,
                len(changes),
                caller.username,
                caller.role,
            )
        return CaptureOutcome(matched, sequence)

    # ── authorization ───────────────────────────────────────────────────

    async def _authorize(self, record: SourceRecord, kind: str, caller: Caller) -> None:
        if not caller.role:
            raise StreamError("STREAM_ACCESS_DENIED", "Select an active role before writing")
        from app.modules.access_control.domain import AccessRequest
        from app.modules.access_control.service import access_control_service

        allowed = await access_control_service.role_allows(
            principal=caller.username,
            active_role=caller.role,
            requests=[
                AccessRequest(_WRITE_ACCESS[kind], "default_catalog", record.database, record.table)
            ],
        )
        if not all(allowed):
            raise StreamError(
                "STREAM_ACCESS_DENIED",
                f"Access denied; role {caller.role} may not {kind.upper()} "
                f"{record.database}.{record.table}",
            )

    # ── planning: the caller's read of the affected rows ────────────────

    def _plan(self, record: SourceRecord, node: Any, sql: str, kind: str) -> dict[str, Any]:
        columns = record.columns
        if kind == "insert":
            if node.OVERWRITE() or node.partitionNames() or node.writeBranch() or node.properties():
                raise unsupported("INSERT OVERWRITE or a partition clause")
            targets = [c.name for c in columns]
            for item in node.insertLabelOrColumnAliases():
                if item.LABEL():
                    raise unsupported("An INSERT label")
                mapping = item.columnAliasesOrByName()
                if mapping is None or mapping.BY():
                    raise unsupported("INSERT BY NAME")
                listed = [_identifier(i) for i in mapping.columnAliases().identifier()]
                if sorted(n.lower() for n in listed) != sorted(n.lower() for n in targets):
                    raise unsupported("An INSERT that does not list every column")
                spelled = {c.name.lower(): c.name for c in columns}
                targets = [spelled[n.lower()] for n in listed]
            aliases = ", ".join(f"__c{i}" for i in range(len(targets)))
            if node.queryStatement() is not None:
                body = f"({_slice(sql, node.queryStatement())}) AS __nova_q({aliases})"
            else:
                rows = node.expressionsWithDefault()
                if any(item.DEFAULT() for row in rows for item in row.expressionOrDefault()):
                    raise unsupported("DEFAULT in INSERT VALUES")
                values = ", ".join(_slice(sql, row) for row in rows)
                body = f"(VALUES {values}) AS __nova_v({aliases})"
            by_name = {c.name: c for c in columns}
            casts = ", ".join(
                f"CAST(__c{i} AS {by_name[name].sql_type}) AS __c{i}"
                for i, name in enumerate(targets)
            )
            return {"sql": f"SELECT {casts} FROM {body}", "targets": targets}
        if kind == "update":
            if node.withClause() is not None or node.fromClause().getChildCount():
                raise unsupported("UPDATE with FROM or WITH")
            by_lower = {c.name.lower(): c for c in columns}
            assignments: list[tuple[str, str]] = []
            for item in node.assignmentList().assignment():
                name = _identifier(item.identifier()).lower()
                if name not in by_lower:
                    raise StreamError("STREAM_SOURCE_WRITE_UNSUPPORTED", f"Unknown column {name}")
                if by_lower[name].name in record.keys:
                    raise unsupported("Updating a primary key column")
                if item.expressionOrDefault().DEFAULT():
                    raise unsupported("SET … = DEFAULT")
                assignments.append(
                    (by_lower[name].name, _slice(sql, item.expressionOrDefault().expression()))
                )
            select = ", ".join(quote_identifier(c.name) for c in columns)
            news = ", ".join(
                f"CAST(({expression}) AS {by_lower[name.lower()].sql_type}) AS __new{i}"
                for i, (name, expression) in enumerate(assignments)
            )
            where = f" WHERE {_slice(sql, node.where)}" if node.where is not None else ""
            return {
                "sql": f"SELECT {select}, {news} FROM {record.qualified}{where}",
                "assignments": [name for name, _ in assignments],
            }
        if kind == "delete":
            if node.withClause() is not None or node.partitionNames() or node.using is not None:
                raise unsupported("DELETE with USING, WITH or a partition")
            select = ", ".join(quote_identifier(c.name) for c in columns)
            where = f" WHERE {_slice(sql, node.where)}" if node.where is not None else ""
            return {"sql": f"SELECT {select} FROM {record.qualified}{where}"}
        raise unsupported(kind.upper())

    async def _changes(
        self, record: SourceRecord, plan: dict[str, Any], kind: str, caller: Caller, writer: Any
    ) -> tuple[list[_Change], int]:
        names = [c.name for c in record.columns]
        _cols, rows = await caller.query(plan["sql"])
        if len(rows) > settings.STREAM_WRITE_MAX_ROWS:
            raise StreamError(
                "STREAM_WRITE_TOO_LARGE",
                f"One write to a stream source changes at most {settings.STREAM_WRITE_MAX_ROWS:,} "
                "rows",
            )
        matched = len(rows)
        targets: dict[str, tuple[dict[str, Any] | None, dict[str, Any] | None]] = {}
        if kind == "insert":
            for row in rows:
                image = dict(zip(plan["targets"], row, strict=True))
                image = {name: image[name] for name in names}
                targets[codec.key_hash(record.columns, record.keys, image)] = (None, image)
            existing = await self._current_images(record, list(targets.values()), writer)
            targets = {key: (existing.get(key), after) for key, (_b, after) in targets.items()}
        elif kind == "update":
            for row in rows:
                before = dict(zip(names, row[: len(names)], strict=True))
                after = dict(before)
                for index, name in enumerate(plan["assignments"]):
                    after[name] = row[len(names) + index]
                targets[codec.key_hash(record.columns, record.keys, before)] = (before, after)
        else:
            for row in rows:
                before = dict(zip(names, row, strict=True))
                targets[codec.key_hash(record.columns, record.keys, before)] = (before, None)
        changes = [
            _Change(key, before, after)
            for key, (before, after) in targets.items()
            if codec.encode_image(record.columns, before)
            != codec.encode_image(record.columns, after)
        ]
        return changes, matched

    async def _current_images(
        self, record: SourceRecord, images: list[Any], writer: Any
    ) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        names = [c.name for c in record.columns]
        by_name = {c.name: c for c in record.columns}
        select = ", ".join(quote_identifier(n) for n in names)
        for start in range(0, len(images), 500):
            batch = images[start : start + 500]
            predicate = " OR ".join(
                "("
                + " AND ".join(
                    f"{quote_identifier(k)} = {codec.literal(by_name[k], after[k])}"
                    for k in record.keys
                )
                + ")"
                for _before, after in batch
            )
            for row in await writer_query(
                writer, f"SELECT {select} FROM {record.qualified} WHERE {predicate}"
            ):
                image = dict(zip(names, row, strict=True))
                found[codec.key_hash(record.columns, record.keys, image)] = image
        return found

    # ── the atomic commit ───────────────────────────────────────────────

    async def _commit(
        self,
        record: SourceRecord,
        kind: str,
        changes: list[_Change],
        sequence: int,
        commit_id: str,
        version_before: int,
        caller: Caller,
        writer: Any,
    ) -> None:
        lineage = await self._lineage(record, [change.key for change in changes], writer)
        journal_rows, lineage_rows = [], []
        for ordinal, change in enumerate(changes, start=1):
            alive = lineage.get(change.key)
            if change.before is None:
                row_id = hashlib.sha256(f"{commit_id}:{change.key}".encode()).hexdigest()
                op = "INSERT"
            else:
                row_id = (
                    alive
                    or hashlib.sha256(
                        f"{record.source_id}:{record.epoch}:base:{change.key}".encode()
                    ).hexdigest()
                )
                op = "DELETE" if change.after is None else "UPDATE"
            before = codec.encode_image(record.columns, change.before)
            after = codec.encode_image(record.columns, change.after)
            digest = journal_digest(
                [
                    sequence,
                    ordinal,
                    commit_id,
                    record.epoch,
                    row_id,
                    op,
                    change.key,
                    before,
                    after,
                    version_before,
                ]
            )
            journal_rows.append(
                "("
                + ", ".join(
                    [
                        str(sequence),
                        str(ordinal),
                        codec.string_literal(commit_id),
                        str(record.epoch),
                        codec.string_literal(row_id),
                        codec.string_literal(op),
                        codec.string_literal(change.key),
                        codec.string_literal(before),
                        codec.string_literal(after),
                        str(version_before),
                        codec.string_literal(caller.username),
                        "NOW()",
                        codec.string_literal(digest),
                    ]
                )
                + ")"
            )
            lineage_rows.append(
                f"({codec.string_literal(change.key)}, {codec.string_literal(row_id)}, "
                f"{'TRUE' if change.after is not None else 'FALSE'}, {sequence})"
            )
        by_name = {c.name: c for c in record.columns}
        if kind == "delete":
            predicate = " OR ".join(
                "("
                + " AND ".join(
                    f"{quote_identifier(k)} = {codec.literal(by_name[k], change.before[k])}"
                    for k in record.keys
                )
                + ")"
                for change in changes
                if change.before is not None
            )
            source_sql = f"DELETE FROM {record.qualified} WHERE {predicate}"
        else:
            names = [c.name for c in record.columns]
            values = ", ".join(
                "(" + ", ".join(codec.literal(by_name[n], change.after[n]) for n in names) + ")"
                for change in changes
                if change.after is not None
            )
            source_sql = (
                f"INSERT INTO {record.qualified} "
                f"({', '.join(quote_identifier(n) for n in names)}) VALUES {values}"
            )
        statements = [
            f"INSERT INTO {record.journal} VALUES {', '.join(journal_rows)}",
            f"INSERT INTO {record.lineage} (key_hash, row_id, alive, commit_seq) VALUES "
            + ", ".join(lineage_rows),
            source_sql,
        ]
        if sum(len(s) for s in statements) > MAX_STATEMENT_BYTES:
            raise StreamError("STREAM_WRITE_TOO_LARGE", "This write is too large to capture")
        async with writer.cursor() as cur:
            await cur.execute("BEGIN")
            try:
                for statement in statements:
                    await cur.execute(statement)
                await cur.execute("COMMIT")
            except Exception as exc:
                # The engine keeps earlier statements of a failed transaction
                # unless it is rolled back explicitly.
                with contextlib.suppress(Exception):  # the original error is the report
                    await cur.execute("ROLLBACK")
                from app.common.sql_guard import redact_sql_credentials

                raise StreamError(
                    "STREAM_WRITE_FAILED", redact_sql_credentials(str(exc))[:500]
                ) from None

    async def _lineage(self, record: SourceRecord, keys: list[str], writer: Any) -> dict[str, str]:
        alive: dict[str, str] = {}
        for start in range(0, len(keys), 1000):
            batch = keys[start : start + 1000]
            listed = ", ".join(codec.string_literal(key) for key in batch)
            for key, row_id, is_alive in await writer_query(
                writer,
                f"SELECT key_hash, row_id, alive FROM {record.lineage} "
                f"WHERE key_hash IN ({listed})",
            ):
                if is_alive:
                    alive[str(key)] = str(row_id)
        return alive


def _identifier(ctx: Any) -> str:
    text = ctx.getText()
    if text.startswith("`") and text.endswith("`"):
        return text[1:-1].replace("``", "`")
    return text


capture_coordinator = CaptureCoordinator()
