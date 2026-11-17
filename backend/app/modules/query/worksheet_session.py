"""Worksheet session: one held connection per run and settings kept per worksheet.

A worksheet normally runs each statement on a connection of its own. A run that
contains a session statement (``SET``, ``SET @var``, ``BEGIN``/``COMMIT``/
``ROLLBACK``), or a run of a worksheet that has remembered settings, runs the
whole script on one connection opened as the caller instead. Every statement
still goes through ``QueryService.execute``: the guard, frontend, Ranger role
activation, security-context check and audit are the ones of the normal path.

Settings and user variables a run leaves behind are remembered for the next run
of the same worksheet: keyed by the sign-in session and the worksheet file,
stored in Redis encrypted with the session key, and applied to the held
connection before the next run's first statement. They are cleared by
``SET <name> = DEFAULT`` (a setting) or ``SET @name = NULL`` (a variable), by a
role switch (the security-context version changes) and by signing out, and they
never reach another worksheet, user, role or the MySQL proxy.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import datetime
import decimal
import json
import logging
import math
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import asyncmy

from app.common.identifiers import check_identifier
from app.core.security import decrypt_password, encrypt_password
from app.modules.query.repository import QueryResult
from app.sql_frontend.session import USER_VARIABLE_NAME, SessionStatement, setting_statement

logger = logging.getLogger(__name__)

#: Limits on what one worksheet keeps between runs.
MAX_SETTINGS = 64
MAX_VARIABLES = 64
MAX_VARIABLE_LITERAL = 16_384
MAX_STATE_BYTES = 262_144

OPEN_TRANSACTION_ROLLED_BACK = (
    "The run ended with a transaction still open, so Nova rolled it back and kept none "
    "of its changes. End the transaction with COMMIT in the same run to keep them."
)
FAILED_TRANSACTION_ROLLED_BACK = (
    "A statement failed inside a transaction, so Nova rolled the transaction back and "
    "kept none of its changes."
)
ROLLBACK_UNKNOWN = (
    "The run ended with a transaction still open and Nova could not confirm the rollback. "
    "The connection was closed, which ends the transaction on the engine."
)
STATE_CLEARED_FOR_ROLE = (
    "Session settings and variables from before your role change were cleared."
)

_HELD: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "nova_worksheet_connection", default=None
)


@contextlib.contextmanager
def holding(connection: Any) -> Iterator[None]:
    token = _HELD.set(connection)
    try:
        yield
    finally:
        _HELD.reset(token)


def is_worksheet_connection(connection: Any) -> bool:
    """True for the connection a worksheet run holds (results are JSON, not wire)."""
    return connection is not None and _HELD.get() is connection


@dataclass
class WorksheetState:
    """Session state one worksheet keeps between runs."""

    principal: str
    active_role: str
    security_context_version: int
    #: Session system variables: name -> ``name = value`` as written.
    settings: dict[str, str] = field(default_factory=dict)
    #: User variables: name -> SQL literal of the value read back from the engine.
    variables: dict[str, str] = field(default_factory=dict)

    def empty(self) -> bool:
        return not self.settings and not self.variables

    def matches(self, principal: str, role: str | None, version: int) -> bool:
        return (
            self.principal == principal
            and self.active_role == role
            and self.security_context_version == version
        )


def _encode(state: WorksheetState) -> str:
    return encrypt_password(
        json.dumps(
            {
                "principal": state.principal,
                "active_role": state.active_role,
                "security_context_version": state.security_context_version,
                "settings": list(state.settings.items()),
                "variables": list(state.variables.items()),
            },
            separators=(",", ":"),
        )
    )


def _decode(value: str) -> WorksheetState:
    data = json.loads(decrypt_password(value))
    return WorksheetState(
        principal=str(data["principal"]),
        active_role=str(data["active_role"]),
        security_context_version=int(data["security_context_version"]),
        settings={str(name): str(text) for name, text in data.get("settings", [])},
        variables={str(name): str(text) for name, text in data.get("variables", [])},
    )


def _store() -> Any:
    from app.core.redis import session_store

    return session_store


async def load_state(
    *,
    session_id: str | None,
    file_id: str | None,
    principal: str,
    role: str | None,
    security_context_version: int,
) -> tuple[WorksheetState | None, list[str]]:
    """The state this worksheet remembers for this caller, and notices for the run.

    State from another role or security-context version is deleted, never applied.
    A store outage means the run starts without remembered state.
    """
    if not session_id or not file_id:
        return None, []
    try:
        raw = await _store().get_worksheet_state(session_id, file_id)
    except Exception:
        logger.warning("Worksheet session state is unavailable", exc_info=True)
        return None, []
    if not raw:
        return None, []
    try:
        state = _decode(raw)
    except Exception:
        state = None
    if state is None or not state.matches(principal, role, security_context_version):
        with contextlib.suppress(Exception):
            await _store().clear_worksheet_state(session_id, file_id)
        notices = [STATE_CLEARED_FOR_ROLE] if state is not None and not state.empty() else []
        return None, notices
    return state, []


async def save_state(
    state: WorksheetState, *, session_id: str | None, file_id: str | None
) -> list[str]:
    """Remember ``state`` for the next run of this worksheet; notices on failure."""
    if not session_id or not file_id:
        return []
    try:
        if state.empty():
            await _store().clear_worksheet_state(session_id, file_id)
            return []
        encoded = _encode(state)
        if len(encoded) > MAX_STATE_BYTES:
            await _store().clear_worksheet_state(session_id, file_id)
            return [
                "This worksheet's session settings and variables are too large to keep for "
                "the next run, so they were not kept."
            ]
        stored = await _store().set_worksheet_state(session_id, file_id, encoded)
    except Exception:
        logger.warning("Worksheet session state could not be stored", exc_info=True)
        stored = False
    if stored:
        return []
    return ["Session settings and variables could not be kept for the next run."]


def _quoted(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


#: Literal forms Nova writes for a remembered user variable; anything else is
#: refused when the state is applied again.
_SAFE_LITERAL = re.compile(
    r"(?:NULL|TRUE|FALSE|[+-]?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?"
    r"|'(?:[^'\\]|\\.|'')*'"
    r"|CAST\('(?:[^'\\]|\\.|'')*' AS (?:DATETIME|DATE)\)"
    r"|parse_json\('(?:[^'\\]|\\.|'')*'\)"
    r"|unhex\('[0-9a-f]*'\))",
    re.DOTALL,
)

class _NotKept(Exception):
    """A user-variable value that has no literal form Nova can apply again."""


def variable_literal(value: Any, type_name: str | None) -> str | None:
    """The SQL literal that restores ``value``; ``None`` for an unset variable.

    ``type_name`` is the engine's ``typeof()`` of the variable. Raises
    ``_NotKept`` for values with no literal Nova applies again: ARRAY, MAP and
    STRUCT values and non-finite numbers.
    """
    kind = (type_name or "").casefold()
    if value is None:
        return None
    if kind.startswith(("array", "map", "struct")):
        raise _NotKept
    if kind == "boolean" or isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _NotKept
        return repr(value)
    if isinstance(value, decimal.Decimal):
        if not value.is_finite():
            raise _NotKept
        return str(value)
    if isinstance(value, datetime.datetime):
        return f"CAST('{value.isoformat(sep=' ')}' AS DATETIME)"
    if isinstance(value, datetime.date):
        return f"CAST('{value.isoformat()}' AS DATE)"
    if isinstance(value, (datetime.time, datetime.timedelta)):
        return _quoted(str(value))
    if isinstance(value, (bytes, bytearray)):
        try:
            value = bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return f"unhex('{bytes(value).hex()}')"
    if isinstance(value, str):
        if kind == "json":
            return f"parse_json({_quoted(value)})"
        if kind == "largeint" and re.fullmatch(r"[+-]?\d+", value):
            return value
        return _quoted(value)
    raise _NotKept


async def _apply_state(
    connection: Any, state: WorksheetState, role: str | None
) -> list[str]:
    """Apply remembered settings, then variables, to the held connection."""
    notices: list[str] = []
    async with connection.cursor() as cursor:
        if role:
            await cursor.execute(f"SET ROLE {check_identifier(role, field='role')}")
        for name, assignment in list(state.settings.items()):
            try:
                setting_statement(assignment)
                await cursor.execute("SET " + assignment)
            except (ValueError, asyncmy.errors.DatabaseError):
                state.settings.pop(name, None)
                notices.append(
                    f"The remembered setting {name} could not be applied again and was cleared."
                )
        for name, literal in list(state.variables.items()):
            try:
                if not USER_VARIABLE_NAME.fullmatch(name) or not _SAFE_LITERAL.fullmatch(
                    literal
                ):
                    raise ValueError("Unexpected remembered value")
                await cursor.execute(f"SET @`{name}` = {literal}")
            except (ValueError, asyncmy.errors.DatabaseError):
                state.variables.pop(name, None)
                notices.append(
                    f"The remembered variable @{name} could not be applied again and was cleared."
                )
    return notices


async def _read_variables(connection: Any, names: list[str]) -> list[tuple[str, Any, Any]]:
    """Read the values of ``names``, with their engine types, from the held connection."""
    if not names:
        return []
    columns = ", ".join(
        f"@`{name}` AS v{index}, typeof(@`{name}`) AS t{index}" for index, name in enumerate(names)
    )
    async with connection.cursor() as cursor:
        await cursor.execute("SELECT " + columns)
        row = await cursor.fetchone()
    return [(name, row[2 * index], row[2 * index + 1]) for index, name in enumerate(names)]


def _record(state: WorksheetState, session: SessionStatement) -> None:
    for change in session.settings:
        state.settings.pop(change.name, None)
        if change.assignment is not None:
            state.settings[change.name] = change.assignment


async def _rollback(connection: Any) -> str:
    async def rollback() -> None:
        async with connection.cursor() as cursor:
            await cursor.execute("ROLLBACK")

    try:
        await asyncio.wait_for(rollback(), timeout=5)
        return "rolled_back"
    except Exception:
        connection.close()
        return "unknown"


@dataclass
class HeldRun:
    """One worksheet run on a held connection: the request and what it changed."""

    service: Any
    statements: list[str]
    sessions: list[SessionStatement]
    username: str
    encrypted_password: str
    database: str | None
    schema: str | None
    role: str | None
    max_rows: int | None
    session_id: str | None
    confirm_destructive: bool
    file_id: str | None
    tenant: str
    security_context_version: int
    allow_stage_export: bool
    metric_source: str
    context_after: Callable[[str, str | None, str | None], tuple[str | None, str | None]]
    error_result: Callable[[str, Exception], QueryResult]
    audit: Callable[..., Any]
    state: WorksheetState | None = None
    notices: list[str] = field(default_factory=list)
    results: list[QueryResult] = field(default_factory=list)
    in_transaction: bool = False
    #: User variables assigned in this run, by case-folded name.
    assigned: dict[str, str] = field(default_factory=dict)
    unkept: list[str] = field(default_factory=list)

    async def run(self) -> list[QueryResult]:
        """Run the checked script on one connection held as the caller."""
        from app.core.database import db

        if self.state is None:
            self.state = WorksheetState(
                self.username, self.role or "", self.security_context_version
            )
        try:
            password = decrypt_password(self.encrypted_password)
            async with db.user_conn(
                username=self.username, password=password, database=None
            ) as connection:
                with holding(connection):
                    self.notices += await _apply_state(connection, self.state, self.role)
                    await self._statements(connection)
                    if self.in_transaction:
                        await self._end_open_transaction(connection)
                    await self._read_back(connection)
        except Exception as exc:
            # Opening the connection or applying the remembered state failed,
            # or the connection broke between statements.
            if not self.results or not self.results[-1].error:
                failed = self.statements[min(len(self.results), len(self.statements) - 1)]
                self.results.append(self.error_result(failed, exc))
            return self._finish()
        self._limit()
        self.unkept += await save_state(
            self.state, session_id=self.session_id, file_id=self.file_id
        )
        return self._finish()

    async def _statements(self, connection: Any) -> None:
        from app.observability.metrics import SQL_SOURCE

        assert self.state is not None
        for stmt_sql, session in zip(self.statements, self.sessions, strict=True):
            metric_token = SQL_SOURCE.set(self.metric_source)
            try:
                result = await self.service.execute(
                    tenant=self.tenant,
                    security_context_version=self.security_context_version,
                    sql=stmt_sql,
                    username=self.username,
                    encrypted_password=self.encrypted_password,
                    database=self.database,
                    schema=self.schema,
                    role=self.role,
                    max_rows=self.max_rows,
                    session_id=self.session_id,
                    confirm_destructive=self.confirm_destructive,
                    allow_stage_export=self.allow_stage_export,
                    file_id=self.file_id,
                    connection=connection,
                    client_transaction=self.in_transaction,
                )
            except Exception as exc:
                result = self.error_result(stmt_sql, exc)
            finally:
                SQL_SOURCE.reset(metric_token)
            self.results.append(result)
            if result.error:
                return
            if session.kind == "begin":
                self.in_transaction = True
            elif session.kind == "end":
                self.in_transaction = False
            elif session.kind == "set":
                _record(self.state, session)
                for name in session.user_variables:
                    self.assigned[name.casefold()] = name
                if session.unnamed_variables:
                    self.unkept.append(
                        "A user variable with a quoted name, or a name longer than 64 "
                        "characters, is kept for this run only."
                    )
            self.database, self.schema = self.context_after(stmt_sql, self.database, self.schema)

    async def _end_open_transaction(self, connection: Any) -> None:
        outcome = await _rollback(connection)
        failed = bool(self.results and self.results[-1].error)
        message = (
            ROLLBACK_UNKNOWN
            if outcome == "unknown"
            else FAILED_TRANSACTION_ROLLED_BACK
            if failed
            else OPEN_TRANSACTION_ROLLED_BACK
        )
        try:
            await self.audit(
                event_type="query",
                user_name=self.username,
                action="transaction_" + outcome,
                object_type="sql",
                object_name=self.database or "workspace",
                status="ERROR",
                error_message=json.dumps({"outcome": outcome, "reason": "run_ended"}),
                active_role=self.role,
                session_id=self.session_id,
                file_id=self.file_id,
                database_name=self.database,
                schema_name=self.schema,
            )
        except Exception:
            logger.exception("Could not audit the worksheet transaction outcome")
        if failed:
            self.results[-1].warnings.append(message)
        else:
            self.results.append(
                QueryResult(
                    original_sql="ROLLBACK",
                    executed_sql="ROLLBACK",
                    error=message,
                    warnings=[message],
                    error_code="transaction_rolled_back",
                )
            )
        self.in_transaction = False
        if outcome == "unknown":
            # The connection is closed; what the run assigned cannot be read back.
            self.assigned.clear()

    async def _read_back(self, connection: Any) -> None:
        assert self.state is not None
        if not self.assigned:
            return
        try:
            values = await _read_variables(connection, list(self.assigned.values()))
        except Exception:
            self.unkept.append("User variables set in this run could not be kept.")
            return
        variables = self.state.variables
        for name, value, type_name in values:
            for known in [key for key in variables if key.casefold() == name.casefold()]:
                variables.pop(known)
            try:
                literal = variable_literal(value, type_name)
            except _NotKept:
                self.unkept.append(
                    f"@{name} holds an ARRAY, MAP or STRUCT value, which is kept for this "
                    "run only."
                )
                continue
            if literal is None:
                continue
            if len(literal) > MAX_VARIABLE_LITERAL:
                self.unkept.append(f"@{name} is too large to keep for the next run.")
                continue
            variables[name] = literal

    def _limit(self) -> None:
        state = self.state
        assert state is not None
        if len(state.settings) > MAX_SETTINGS or len(state.variables) > MAX_VARIABLES:
            self.unkept.append(
                f"A worksheet keeps at most {MAX_SETTINGS} settings and {MAX_VARIABLES} "
                "variables between runs; the oldest were dropped."
            )
            state.settings = dict(list(state.settings.items())[-MAX_SETTINGS:])
            state.variables = dict(list(state.variables.items())[-MAX_VARIABLES:])

    def _finish(self) -> list[QueryResult]:
        if self.results:
            self.results[0].warnings[:0] = self.notices
            self.results[-1].warnings.extend(dict.fromkeys(self.unkept))
        return self.results
