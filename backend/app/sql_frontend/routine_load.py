"""Routine Load statements: planned by Nova, run by the engine as the caller.

``CREATE``, ``ALTER``, ``PAUSE``, ``RESUME`` and ``STOP ROUTINE LOAD`` are
decoded from the pinned grammar here, validated into a credential-free
:class:`RoutineLoadPayload`, and executed by
:mod:`app.modules.routine_load.service`. ``SHOW ROUTINE LOAD`` and ``SHOW
ROUTINE LOAD TASK`` stay native: the engine lists the jobs the caller may see.

``FROM KAFKA`` must name a Nova Kafka connection (``"kafka_connection"``).
Broker lists and every security or credential property are refused, so a
broker secret is never written in SQL text, history or audit; Nova adds them
from the connection at execution, after authorization.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from app.common.sql_guard import is_credential_property
from app.sql_frontend.analysis.effects import PlanEffects
from app.sql_frontend.ast.statements import Statement
from app.sql_frontend.errors import CapabilityUnsupportedError, SemanticError
from app.sql_frontend.parser import ParsedStatement
from app.sql_frontend.planning.execution import ActionKind, NovaActionPlan, SourcePayload

_OPERATIONS = {
    "CreateRoutineLoadStatementContext": "create",
    "AlterRoutineLoadStatementContext": "alter",
    "PauseRoutineLoadStatementContext": "pause",
    "ResumeRoutineLoadStatementContext": "resume",
    "StopRoutineLoadStatementContext": "stop",
}
CONTEXTS = tuple(_OPERATIONS)
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0"}
CONNECTION_PROPERTY = "kafka_connection"

#: ``FROM KAFKA`` keys the connection owns: where the brokers are and how to
#: sign in to them.
_CONNECTION_OWNED = ("kafka_broker_list", "property.security.", "property.sasl.",
                     "property.ssl.", "property.bootstrap.servers")


@dataclass(frozen=True, slots=True)
class RoutineLoadStatement(Statement):
    operation: str
    database: str | None
    name: str
    table: str | None = None
    source: str | None = None
    #: ``(key, value, verbatim text)`` of each ``FROM KAFKA`` property.
    source_properties: tuple[tuple[str, str, str], ...] = ()
    #: Character span of the ``FROM KAFKA`` property list, parentheses included.
    source_span: tuple[int, int] | None = None
    #: ``(key, value)`` of each job property (``PROPERTIES (...)``).
    job_properties: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RoutineLoadPayload(SourcePayload):
    """A validated routine-load statement. Carries no broker address or credential."""

    operation: str
    database: str
    name: str
    table: str | None = None
    connection: str | None = None
    topic: str | None = None
    data_format: str = "csv"
    sql_before: str = ""
    source_properties: tuple[str, ...] = ()
    sql_after: str = ""
    #: The statement as written, for ALTER/PAUSE/RESUME/STOP.
    sql: str = ""


def _name(node: Any) -> str:
    from app.sql_frontend.streams import name_parts

    return ".".join(name_parts(node))


def _string(parsed: ParsedStatement, node: Any) -> str:
    text = parsed.normalized_sql[node.start.start : node.stop.stop + 1]
    if text[:1] in {"'", '"'} and text[-1:] == text[:1]:
        # Values are only inspected (keys, format, topic); the statement keeps the
        # caller's text, so a JSON path such as "[\"$.id\"]" reaches the engine as written.
        body = text[1:-1].replace(text[0] * 2, text[0])
        return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), body)
    return text


def _properties(parsed: ParsedStatement, node: Any) -> tuple[tuple[str, str, str], ...]:
    if node is None:
        return ()
    return tuple(
        (
            _string(parsed, prop.key).strip(),
            _string(parsed, prop.value),
            parsed.normalized_sql[prop.start.start : prop.stop.stop + 1],
        )
        for prop in node.property_()
    )


def build_routine_load_statement(parsed: ParsedStatement) -> RoutineLoadStatement:
    node = parsed.statement_context
    operation = _OPERATIONS[type(node).__name__]
    database = _name(node.db) if node.db is not None else None
    name = _name(node.name)
    if operation in {"pause", "resume", "stop"}:
        return RoutineLoadStatement(parsed, operation, database, name)
    source_node = node if operation == "create" else node.dataSource()
    source = source_props = span = None
    if source_node is not None:
        source = _name(source_node.source).upper()
        props = source_node.dataSourceProperties()
        if props is not None:
            plist = props.propertyList()
            source_props = _properties(parsed, plist)
            span = (plist.start.start, plist.stop.stop + 1)
    job = node.jobProperties()
    job_props = (
        tuple((key, value) for key, value, _ in _properties(
            parsed, job.properties().propertyList()
        ))
        if job is not None else ()
    )
    return RoutineLoadStatement(
        parsed, operation, database, name,
        table=_name(node.table) if operation == "create" else None,
        source=source,
        source_properties=source_props or (),
        source_span=span,
        job_properties=job_props,
    )


def routine_load_effects(statement: RoutineLoadStatement) -> PlanEffects:
    if statement.operation == "create":
        return PlanEffects(writes_data=True, writes_metadata=True, external_io=True)
    if statement.operation == "resume":
        return PlanEffects(writes_data=True, external_io=True, writes_metadata=True)
    if statement.operation == "stop":
        # A stopped job cannot be resumed.
        return PlanEffects(writes_metadata=True, drops_objects=True)
    return PlanEffects(writes_metadata=True)


def _owned_by_connection(key: str) -> bool:
    lowered = key.lower()
    return is_credential_property(lowered) or any(
        lowered == owned or (owned.endswith(".") and lowered.startswith(owned))
        for owned in _CONNECTION_OWNED
    )


def validate_routine_load(statement: Statement, context: Any) -> RoutineLoadPayload:
    if not isinstance(statement, RoutineLoadStatement):
        raise TypeError("Routine load validation requires a routine load statement")
    database = statement.database or (context.database if context is not None else None)
    if not database:
        raise SemanticError("Name the job's database: <database>.<job>, or select a database")
    if "." in database:
        raise SemanticError("Routine loads write to Nova tables: name a database, not a catalog")
    sql = statement.parsed.normalized_sql
    if statement.operation in {"pause", "resume", "stop"}:
        return RoutineLoadPayload(0, statement.operation, database, statement.name, sql=sql)
    if statement.source is not None and statement.source != "KAFKA":
        raise CapabilityUnsupportedError(
            "Nova loads routine loads from Kafka connections only"
        )
    keys = [key.lower() for key, _, _ in statement.source_properties]
    for key, value, _ in statement.source_properties:
        lowered = key.lower()
        if lowered == CONNECTION_PROPERTY:
            continue
        if _owned_by_connection(lowered):
            raise SemanticError(
                f"'{key}' comes from the Kafka connection; name the connection with "
                f"\"{CONNECTION_PROPERTY}\" = \"<connection>\" instead"
            )
        if "@" in value and lowered == "confluent.schema.registry.url":
            raise SemanticError("The schema registry URL cannot carry a user or password")
    for key, _ in statement.job_properties:
        if is_credential_property(key):
            raise SemanticError(f"Property '{key}' cannot carry a credential")
    if statement.operation == "alter":
        for banned in (CONNECTION_PROPERTY, "kafka_topic"):
            if banned in keys:
                raise SemanticError(
                    f"ALTER ROUTINE LOAD cannot change '{banned}'; create a new job instead"
                )
        return RoutineLoadPayload(0, "alter", database, statement.name, sql=sql)

    if statement.table is None or "." in statement.table:
        raise SemanticError("ON names a table in the job's database")
    if keys.count(CONNECTION_PROPERTY) != 1 or statement.source_span is None:
        raise SemanticError(
            "FROM KAFKA names a Nova Kafka connection: "
            f"(\"{CONNECTION_PROPERTY}\" = \"<connection>\", \"kafka_topic\" = \"<topic>\", ...)"
        )
    values = {key.lower(): value for key, value, _ in statement.source_properties}
    topic = values.get("kafka_topic", "").strip()
    if not topic:
        raise SemanticError("FROM KAFKA needs \"kafka_topic\"")
    from app.modules.catalog.connections import safe_name

    try:
        connection = safe_name(values[CONNECTION_PROPERTY].strip())
    except Exception:  # noqa: BLE001 - shown as a statement error
        raise SemanticError(
            "kafka_connection names a Nova connection: letters, digits and underscores"
        ) from None
    data_format = {key.lower(): value for key, value in statement.job_properties}.get(
        "format", "csv"
    ).strip().lower()
    if data_format not in {"csv", "json", "avro"}:
        raise SemanticError("format is csv, json or avro")
    start, end = statement.source_span
    return RoutineLoadPayload(
        0, "create", database, statement.name,
        table=statement.table,
        connection=connection,
        topic=topic[:512],
        data_format=data_format,
        sql_before=sql[:start],
        source_properties=tuple(
            text for key, _, text in statement.source_properties
            if key.lower() != CONNECTION_PROPERTY
        ),
        sql_after=sql[end:],
        sql=sql,
    )


class RoutineLoadPlanner:
    async def plan(self, logical, context) -> NovaActionPlan:
        payload = context.validated[logical.source_key]
        from dataclasses import replace

        return NovaActionPlan(
            ActionKind.ROUTINE_LOAD,
            replace(payload, source_key=logical.source_key),
            logical.analysis.effects,
            logical.analysis.requires_confirmation,
        )


async def execute_routine_load(plan: NovaActionPlan, context: Any):
    """Run a routine-load statement through the routine-load service as the caller."""
    from app.core.exceptions import StarRocksError
    from app.core.security import decrypt_password
    from app.modules.access_control.security_context import require_security_context
    from app.modules.query.repository import QueryResult, query_repo
    from app.modules.routine_load.service import (
        CreateRequest,
        EngineReply,
        RoutineLoadError,
        routine_load_service,
    )

    payload = plan.payload
    if not isinstance(payload, RoutineLoadPayload):
        raise SemanticError("Routine load action requires a typed payload")
    parsed = context.statements[payload.source_key].parsed
    start = time.monotonic()
    security = require_security_context(
        principal=context.username,
        active_role=context.role,
        database=context.database,
        session_id=context.session_id,
        security_context_version=context.security_context_version,
    )
    password = "" if context.connection is not None else decrypt_password(
        context.encrypted_password
    )

    async def run(sql: str) -> EngineReply:
        result = await query_repo.execute_as_user(
            sql=sql, username=context.username, password=password,
            database=payload.database, role=context.role, connected=context.connection,
        )
        if result.error:
            raise StarRocksError(result.error)
        return EngineReply(list(result.columns or []), [list(row) for row in result.rows or []])

    executed = parsed.normalized_sql
    try:
        if payload.operation == "create":
            executed = await routine_load_service.create(
                CreateRequest(
                    database=payload.database,
                    job_name=payload.name,
                    table_name=payload.table or "",
                    connection_name=payload.connection or "",
                    topic=payload.topic or "",
                    data_format=payload.data_format,
                    sql_before=payload.sql_before,
                    source_properties=payload.source_properties,
                    sql_after=payload.sql_after,
                ),
                security,
                run,
            )
        else:
            await routine_load_service.control(
                payload.operation, payload.database, payload.name, payload.sql, security, run
            )
    except RoutineLoadError as exc:
        return QueryResult(
            error=str(exc),
            error_code="routine_load_error",
            elapsed_ms=round((time.monotonic() - start) * 1000, 2),
            original_sql=parsed.original_sql,
            executed_sql=parsed.normalized_sql,
        )
    return QueryResult(
        columns=["Result"],
        rows=[[f"Routine load {payload.database}.{payload.name}: {payload.operation} done"]],
        row_count=1,
        elapsed_ms=round((time.monotonic() - start) * 1000, 2),
        original_sql=parsed.original_sql,
        executed_sql=executed,
    )


# ── SHOW output: connection names instead of broker addresses ─────────────

#: Statements whose rows describe routine-load jobs as the engine stores them.
SHOW_CONTEXTS = frozenset({
    "ShowRoutineLoadStatementContext",
    "ShowCreateRoutineLoadStatementContext",
    "ShowRoutineLoadTaskStatementContext",
})
HIDDEN = "hidden"
_BROKER_JSON = re.compile(r'("brokerList"\s*:\s*)"((?:[^"\\]|\\.)*)"')
_BROKER_PROPERTY = re.compile(r'("kafka_broker_list"\s*=\s*)"((?:[^"\\]|\\.)*)"', re.IGNORECASE)
_SIGN_IN_JSON = re.compile(
    r'("(?:property\.)?(?:sasl\.(?:password|jaas\.config)|ssl\.[a-z.]*password)"'
    r'\s*:\s*)"(?:[^"\\]|\\.)*"', re.IGNORECASE,
)
_SIGN_IN_PROPERTY = re.compile(
    r'("(?:property\.)?(?:sasl\.(?:password|jaas\.config)|ssl\.[a-z.]*password)"'
    r'\s*=\s*)"(?:[^"\\]|\\.)*"', re.IGNORECASE,
)


def logical_routine_load_text(text: str, brokers: list[tuple[str, str]]) -> str:
    """A routine-load ``SHOW`` value without broker addresses or passwords.

    ``brokers`` maps a Kafka connection's broker list to its name. A known
    broker list becomes ``kafka_connection`` with that name; any other becomes
    ``hidden``. Passwords are masked; the sign-in user stays visible.
    """
    if "broker" not in text.lower() and "sasl" not in text.lower() and "ssl" not in text.lower():
        return text
    names = dict(brokers)

    def name_of(value: str) -> str:
        return names.get(value.strip(), HIDDEN)

    text = _BROKER_JSON.sub(lambda m: f'"kafkaConnection":"{name_of(m.group(2))}"', text)
    text = _BROKER_PROPERTY.sub(
        lambda m: f'"{CONNECTION_PROPERTY}" = "{name_of(m.group(2))}"', text
    )
    text = _SIGN_IN_JSON.sub(lambda m: f'{m.group(1)}"***"', text)
    return _SIGN_IN_PROPERTY.sub(lambda m: f'{m.group(1)}"***"', text)


async def broker_connection_map(username: str, role: str | None) -> list[tuple[str, str]]:
    """``(broker list, connection name)`` of every Kafka connection.

    Read as the caller. Without a readable answer the map is empty, so every
    broker list is shown as ``hidden``: the rewrite never fails open.
    """
    from app.modules.catalog.metadata_repository import CatalogMetadataRepository
    from app.modules.routine_load.repository import routine_load_transaction
    from app.modules.stages.service import caller_actor

    try:
        async with routine_load_transaction(
            caller_actor(username, role), read_only=True
        ) as repo:
            rows = await CatalogMetadataRepository(repo.connection, repo.actor).list_connections()
    except Exception:  # noqa: BLE001 - an empty map hides every broker list
        return []
    return [
        (str(row["host"]).strip(), str(row["name"]))
        for row in rows
        if row.get("connector_type") == "kafka" and row.get("host")
    ]


def register_handler() -> None:
    from app.sql_frontend.execution.executor import action_handler_registry

    if RoutineLoadPayload not in action_handler_registry.handlers:
        action_handler_registry.register(RoutineLoadPayload, execute_routine_load)


register_handler()
