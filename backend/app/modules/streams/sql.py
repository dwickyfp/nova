"""Where Streams meet SQL execution (called from ``FeatureAdapters.execute_engine``).

For each native statement Nova is about to send to the engine:

* a write (INSERT / UPDATE / DELETE / MERGE / TRUNCATE) to a table that has
  Streams goes to the capture coordinator; other changes to such a table
  (INSERT OVERWRITE, ALTER) are refused while it has Streams;
* a reference to a Stream (``FROM db.my_stream``) is replaced by the stream's
  rows as a typed derived table, and ``SYSTEM$STREAM_HAS_DATA('db.s')`` (or the
  older ``NOVA_STREAM_HAS_DATA``) by TRUE or FALSE;
* an ``INSERT INTO target … SELECT … FROM <stream>`` *consumes*: it runs with a
  label derived from the stream offset and advances the offset only after the
  engine shows the insert visible. Other statements only read (peek).

Statements that touch none of this cost one cached lookup and run unchanged.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from app.core.config import settings
from app.modules.query.repository import QueryResult
from app.modules.streams.capture import Caller, capture_coordinator
from app.modules.streams.consumption import ConsumptionState
from app.modules.streams.namespace import StreamName, resolve_stream_name
from app.modules.streams.schemas import StreamError
from app.modules.streams.service import StreamCaller, stream_service
from app.modules.streams.sources import source_registry

_SOURCE_DDL = {"AlterTableStatementContext": "ALTER TABLE"}
_TRUNCATE = "TruncateTableStatementContext"


class StreamSql:
    def __init__(self) -> None:
        self._names: set[tuple[str, str]] = set()
        self._names_at = 0.0

    async def _stream_names(self) -> set[tuple[str, str]]:
        if time.monotonic() - self._names_at < settings.STREAM_REGISTRY_CACHE_SECONDS:
            return self._names
        try:
            rows = await stream_service.repository.current_generations(
                statuses=("READY", "STALE")
            )
            self._names = {
                (str(row["database_name"]).lower(), str(row["name"]).lower()) for row in rows
            }
        except Exception:  # noqa: BLE001 - unknown names fall through to the engine
            self._names = set()
        self._names_at = time.monotonic()
        return self._names

    def forget(self) -> None:
        self._names_at = 0.0

    # ── entry point ─────────────────────────────────────────────────────

    async def intercept(self, adapters: Any, plan: Any, context: Any) -> QueryResult | None:
        statement = context.statements[plan.source_key]
        parsed = statement.parsed
        root = type(parsed.statement_context).__name__
        captured = await source_registry.captured_tables()
        intent = plan.transaction_intent
        if captured:
            ddl = root in _SOURCE_DDL or root == _TRUNCATE
            target = self._ddl_target(parsed, context.database) if ddl else None
            if target is not None and target in captured and root in _SOURCE_DDL:
                return self._error(
                    parsed,
                    "STREAM_SOURCE_WRITE_UNSUPPORTED",
                    f"{_SOURCE_DDL[root]} on a table with streams is not supported; drop its "
                    "streams first",
                )
            if target is not None and target in captured:
                try:
                    return await self._capture(
                        adapters, plan, context, captured[target], "truncate"
                    )
                except StreamError as exc:
                    return self._error(parsed, exc.code, str(exc))
        streams = await self._stream_refs(parsed, context.database)
        functions = tuple(plan.stream_functions or ())
        writes_source = (
            intent is not None
            and captured
            and (intent.target[1].lower(), intent.target[2].lower()) in captured
        )
        if not (streams or functions or writes_source):
            return None
        caller = StreamCaller(
            context.username, context.role, int(context.security_context_version or 1)
        )
        try:
            if writes_source:
                if streams or functions:
                    raise StreamError(
                        "STREAM_TARGET_ATOMICITY_UNSUPPORTED",
                        "A statement cannot both read a stream and write a table with streams",
                    )
                record = captured[(intent.target[1].lower(), intent.target[2].lower())]
                return await self._capture(adapters, plan, context, record, intent.kind)
            return await self._read(adapters, plan, context, caller, streams, functions)
        except StreamError as exc:
            return self._error(parsed, exc.code, str(exc))

    # ── writes to a source ──────────────────────────────────────────────

    async def _capture(self, adapters: Any, plan: Any, context: Any, record: Any, kind: str):
        parsed = context.statements[plan.source_key].parsed
        if context.transaction_active:
            raise StreamError(
                "STREAM_SOURCE_WRITE_UNSUPPORTED",
                "Writes to a table with streams cannot run inside an explicit transaction",
            )

        async def query(sql: str) -> tuple[list[str], list[list[Any]]]:
            result = await self._as_caller(
                adapters, context, sql, settings.STREAM_WRITE_MAX_ROWS + 1
            )
            if result.error:
                raise StreamError("STREAM_WRITE_FAILED", str(result.error))
            return list(result.columns), [list(row) for row in result.rows]

        merge = getattr(plan, "capture", None)

        async def run_native() -> QueryResult:
            if merge is None:
                return await adapters.execute_engine_sql(plan, context)
            from dataclasses import replace

            result = QueryResult()
            for step in merge.native_steps:
                result = await adapters.execute_engine_sql(
                    replace(plan, engine_sql=step, redacted_sql=step, capture=None), context
                )
                if result.error:
                    break
            return result

        caller = Caller(context.username, context.role, query, run_native)
        started = time.monotonic()
        outcome = await capture_coordinator.write(
            record, parsed.statement_context, parsed.normalized_sql, kind, caller, merge
        )
        if outcome.native is not None:
            return outcome.native
        elapsed = round((time.monotonic() - started) * 1000, 2)
        await adapters._audit(
            event_type="query",
            user_name=context.username,
            action="execute",
            object_type="sql",
            object_name=record.database,
            status="SUCCESS",
            sql_text=parsed.original_sql,
            rewritten_sql=parsed.original_sql,
            duration_ms=int(elapsed),
            rows_affected=outcome.affected_rows,
            session_id=context.session_id,
            file_id=context.file_id,
            database_name=context.database,
            schema_name=context.schema,
            active_role=context.role,
        )
        return QueryResult(
            affected_rows=outcome.affected_rows,
            elapsed_ms=elapsed,
            original_sql=parsed.original_sql,
            executed_sql=parsed.original_sql,
            warnings=["Captured for the table's streams."],
        )

    # ── reads and consumption ───────────────────────────────────────────

    async def _read(
        self,
        adapters: Any,
        plan: Any,
        context: Any,
        caller: StreamCaller,
        streams: list[tuple[StreamName, Any]],
        functions: tuple[Any, ...],
    ) -> QueryResult:
        parsed = context.statements[plan.source_key].parsed
        if plan.private_bindings:
            raise StreamError("STREAM_QUERY_UNSUPPORTED", "Credentials cannot be used with streams")
        intent = plan.transaction_intent
        root = type(parsed.statement_context).__name__
        consuming = bool(streams) and intent is not None
        if consuming and (intent.kind != "insert" or root != "InsertStatementContext"):
            raise StreamError(
                "STREAM_TARGET_ATOMICITY_UNSUPPORTED",
                "Only INSERT INTO … SELECT … FROM a stream consumes it; UPDATE, DELETE, "
                "INSERT OVERWRITE and CREATE TABLE … AS cannot read a stream",
            )
        if streams and not intent and root not in {"QueryStatementContext"}:
            raise StreamError(
                "STREAM_QUERY_UNSUPPORTED", "Streams can be read by SELECT and INSERT"
            )
        if consuming and len({name for name, _node in streams}) > 1:
            raise StreamError(
                "STREAM_TARGET_ATOMICITY_UNSUPPORTED",
                "One consuming statement reads one stream; consume each stream separately",
            )
        if consuming and context.transaction_active:
            raise StreamError(
                "STREAM_TARGET_ATOMICITY_UNSUPPORTED",
                "Streams cannot be consumed inside an explicit transaction",
            )
        from app.observability.metrics import (
            STREAM_CONSUMPTIONS,
            STREAM_READ_DURATION,
            STREAM_READS,
        )

        read_started = time.monotonic()
        resolved: dict[StreamName, Any] = {}
        for name in {name for name, _node in streams} | {f.name for f in functions}:
            resolved[name] = await stream_service.resolve(name, caller)
        replacements: list[tuple[int, int, str]] = []
        for name, node in streams:
            rows = await stream_service.rows(resolved[name])
            alias = (
                node.identifier().getText()
                if node.identifier() is not None
                else "`" + name.name.replace("`", "``") + "`"
            )
            replacements.append(
                (
                    node.start.start,
                    node.stop.stop + 1,
                    f"{stream_service.relation_sql(resolved[name], rows)} AS {alias}",
                )
            )
        for binding in functions:
            replacements.append(
                (binding.start, binding.end, "TRUE" if resolved[binding.name].has_data else "FALSE")
            )
        from app.sql_frontend.literals import typed_literal_rewrites

        for rewrite in typed_literal_rewrites(parsed):
            replacements.append((rewrite.start, rewrite.end, rewrite.slot))
        label_at = self._label_position(parsed) if consuming else None
        if label_at is not None:
            replacements.append((label_at, label_at, " WITH LABEL __NOVA_STREAM_LABEL__ "))
        sql = _apply(parsed.normalized_sql, replacements)
        STREAM_READ_DURATION.observe(time.monotonic() - read_started)
        STREAM_READS.labels("consume" if consuming else "peek").inc()
        if not consuming:
            return await adapters.execute_engine_sql(plan, context, sql_override=sql)

        name = streams[0][0]
        stream = resolved[name]
        if not stream.has_data:
            # Nothing to consume: the statement inserts no rows and, as in
            # Snowflake, the offset does not move (no claim, no label).
            STREAM_CONSUMPTIONS.labels("empty").inc()
            return await adapters.execute_engine_sql(
                plan, context, sql_override=sql.replace(" WITH LABEL __NOVA_STREAM_LABEL__ ", " ")
            )
        target_db, target_table = intent.target[1] or (context.database or ""), intent.target[2]
        explain = await self._as_caller(
            adapters, context, "EXPLAIN " + sql.replace("__NOVA_STREAM_LABEL__", "nova_explain"), 1
        )
        if explain.error:
            return QueryResult(
                error=str(explain.error),
                original_sql=parsed.original_sql,
                executed_sql=parsed.original_sql,
            )
        outcome: dict[str, Any] = {}

        async def submit(label: str) -> None:
            try:
                outcome["result"] = await adapters.execute_engine_sql(
                    plan, context, sql_override=sql.replace("__NOVA_STREAM_LABEL__", label)
                )
            except Exception as exc:
                outcome["error"] = exc
                raise

        login = await self._as_caller(adapters, context, "SELECT USER()", 1)
        if login.error or not login.rows:
            raise StreamError("STREAM_CONSUMPTION_BLOCKED", "The session's login is unknown")
        # The engine records a load under the session's *login* account: under
        # a scheduled run's impersonation that is the worker, not the task's
        # user. The receipt must name who the engine will report.
        engine_principal = str(login.rows[0][0]).split("@")[0].strip("'`")
        digest = hashlib.sha256(parsed.normalized_sql.encode()).hexdigest()
        try:
            state = await stream_service.consume(
                stream, caller, target_db, target_table, digest, submit,
                engine_principal=engine_principal,
            )
        except StreamError as exc:
            STREAM_CONSUMPTIONS.labels(
                "conflict" if exc.code == "STREAM_CONSUMPTION_BLOCKED" else "error"
            ).inc()
            raise
        STREAM_CONSUMPTIONS.labels(
            {
                ConsumptionState.OFFSET_COMMITTED: "committed",
                ConsumptionState.FAILED: "failed",
            }.get(state, "unverified")
        ).inc()
        if "error" in outcome and state != ConsumptionState.OFFSET_COMMITTED:
            raise outcome["error"]
        result = outcome.get("result") or QueryResult(original_sql=parsed.original_sql)
        if state == ConsumptionState.OFFSET_COMMITTED:
            result.warnings = [
                *(result.warnings or []),
                f"Stream {name.database}.{name.name} consumed.",
            ]
            return result
        if state == ConsumptionState.FAILED:
            return (
                result
                if result.error
                else QueryResult(
                    error="The insert failed; the stream was not consumed",
                    original_sql=parsed.original_sql,
                )
            )
        raise StreamError(
            "STREAM_CONSUMPTION_BLOCKED",
            "The insert's outcome could not be verified; the stream offset did not move",
        )

    # ── helpers ─────────────────────────────────────────────────────────

    async def _stream_refs(self, parsed: Any, database: str | None) -> list[tuple[StreamName, Any]]:
        names = await self._stream_names()
        if not names:
            return []
        from app.sql_frontend.antlr_utils import walk_nodes
        from app.sql_frontend.streams import name_parts

        found = []
        for node in walk_nodes(parsed.statement_context):
            if type(node).__name__ != "TableAtomContext":
                continue
            parts = name_parts(node.qualifiedName())
            if len(parts) > 3 or (len(parts) == 3 and parts[1].casefold() != "default"):
                continue
            try:
                name = resolve_stream_name(parts, database)
            except Exception:  # noqa: BLE001 - not a stream-shaped name
                continue
            if (name.database.lower(), name.name.lower()) in names:
                found.append((name, node))
        return found

    @staticmethod
    def _ddl_target(parsed: Any, database: str | None) -> tuple[str, str] | None:
        node = parsed.statement_context
        qualified = getattr(node, "qualifiedName", None)
        if qualified is None:
            return None
        target = qualified()
        if isinstance(target, list):
            target = target[0] if target else None
        if target is None:
            return None
        from app.sql_frontend.streams import name_parts

        parts = name_parts(target)
        if len(parts) == 1 and database:
            return database.lower(), parts[0].lower()
        if len(parts) >= 2:
            return parts[-2].lower(), parts[-1].lower()
        return None

    @staticmethod
    def _label_position(parsed: Any) -> int:
        node = parsed.statement_context
        if any(item.LABEL() for item in node.insertLabelOrColumnAliases()):
            raise StreamError(
                "STREAM_TARGET_ATOMICITY_UNSUPPORTED",
                "A consuming INSERT takes Nova's label; remove WITH LABEL",
            )
        anchor = node.partitionNames() or node.qualifiedName()
        return anchor.stop.stop + 1

    @staticmethod
    async def _as_caller(adapters: Any, context: Any, sql: str, max_rows: int) -> QueryResult:
        password = (
            "" if context.connection is not None else adapters._decrypt(context.encrypted_password)
        )
        try:
            return await adapters._repo.execute_as_user(
                sql=sql,
                username=context.username,
                password=password,
                database=context.database,
                role=context.role,
                max_rows=max_rows,
                connected=context.connection,
                **({"session_prepared": True} if context.engine_session_prepared else {}),
            )
        except Exception as exc:  # noqa: BLE001 - reported as a query error
            from app.common.sql_guard import redact_sql_credentials

            return QueryResult(error=redact_sql_credentials(str(exc))[:500])

    @staticmethod
    def _error(parsed: Any, code: str, message: str) -> QueryResult:
        return QueryResult(
            error=message,
            error_code=code,
            original_sql=parsed.original_sql,
            executed_sql=parsed.original_sql,
        )


def _apply(sql: str, replacements: list[tuple[int, int, str]]) -> str:
    ordered = sorted(replacements, key=lambda item: (item[0], item[1]))
    for left, right in zip(ordered, ordered[1:], strict=False):
        if left[1] > right[0]:
            raise StreamError("STREAM_QUERY_UNSUPPORTED", "Overlapping stream rewrites")
    for start, end, text in reversed(ordered):
        sql = sql[:start] + text + sql[end:]
    return sql


stream_sql = StreamSql()
