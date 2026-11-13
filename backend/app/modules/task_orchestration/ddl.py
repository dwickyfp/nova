"""Lower Nova ``CREATE TASK`` DDL to metadata + the engine's ``SUBMIT TASK``.

``CREATE TASK … AFTER / FINALIZE / WHEN / SCHEDULE / OVERLAP_POLICY`` is a Nova
surface, not an engine statement (design §1, §3). StarRocks 4.1.1 has only
``SUBMIT TASK``. This module turns the surface into Nova metadata (a task row
plus edge rows) and the body the worker submits.

It is **pure**: parsing, validation and normalisation only, no database and no
connection, so the whole surface is testable without an engine. The caller
persists the result, which keeps authorization on the caller's connection.

Validation the grammar cannot express, and which therefore lives here:

* **clause order** — ``taskClause*`` is variadic, so ``SCHEDULE`` before
  ``AFTER`` parses; the surface's order is ``AFTER, FINALIZE, WHEN,
  OVERLAP_POLICY, SCHEDULE``.
* **duplicate clauses** — each clause may appear at most once.
* **`OVERLAP_POLICY` value** — the grammar accepts any ``identifierOrString``;
  only the Nova enum values are stored.
* **cron validity** — through the existing Nova cron parser
  (:func:`app.modules.task_orchestration.schedule.parse_cron`), never a new
  regex.
* **cycles** — through :func:`app.modules.task_orchestration.graph.validate_graph`.
* **`[=]` normalisation** — ``FINALIZE b`` and ``FINALIZE = b`` are one form.

Text is sliced out of the original statement (by token offsets), never rebuilt
from ``getText()``: ``getText()`` drops inter-token whitespace, which would turn
``INSERT INTO t`` into ``INSERTINTOt`` and corrupt the body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.modules.task_orchestration.graph import (
    Edge,
    Graph,
    GraphValidationError,
    validate_graph,
)
from app.modules.task_orchestration.schedule import (
    ScheduleError,
    parse_cron,
    parse_interval,
    resolve_timezone,
)
from app.modules.task_orchestration.schemas import OverlapPolicy

#: The surface's clause order. `taskClause*` accepts any order, so the lowering
#: enforces this one and rejects anything out of order rather than guessing.
CLAUSE_ORDER = (
    "AFTER",
    "FINALIZE",
    "WHEN",
    "OVERLAP_POLICY",
    "SCHEDULE",
    "LANGUAGE",
    "RUNTIME_VERSION",
    "PACKAGES",
)

#: How a task's body runs. ``sql`` is one statement submitted as a native
#: one-shot ``SUBMIT TASK``; ``sql_script`` is a statement block Nova runs in
#: order through the governed SQL path; ``python`` runs in a sandbox runtime.
USER_HANDLERS = frozenset({"sql", "sql_script", "python"})
#: The user handlers Nova executes itself rather than as a native engine task.
SCRIPT_HANDLERS = frozenset({"sql_script", "python"})

#: Python versions a task may ask for: the notebook runtime image's interpreter.
PYTHON_RUNTIME_VERSIONS = ("3.12",)
DEFAULT_PYTHON_RUNTIME = PYTHON_RUNTIME_VERSIONS[0]
#: Bounds of a script body.
MAX_SCRIPT_STATEMENTS = 100
MAX_PYTHON_SOURCE_BYTES = 512 * 1024
MAX_TASK_PACKAGES = 30

#: Statements a script task may not contain: they would nest tasks, switch the
#: run's identity, or control a transaction the worker does not own.
_FORBIDDEN_SCRIPT_STATEMENTS = {
    "SubmitTaskStatementContext": "a task cannot create tasks",
    "AlterTaskStatementContext": "a task cannot alter tasks",
    "DropTaskStatementContext": "a task cannot drop tasks",
    "NovaExecuteTaskStatementContext": "a task cannot run other tasks",
    "SetRoleStatementContext": "a task runs under its owner role and cannot switch roles",
    "SetDefaultRoleStatementContext": "a task cannot change default roles",
    "ExecuteAsStatementContext": "a task cannot change its user",
    "BeginStatementContext": "a task script cannot open a transaction",
    "CommitStatementContext": "a task script cannot commit a transaction",
    "RollbackStatementContext": "a task script cannot roll back a transaction",
}

#: Nova's overlap-policy values. The grammar accepts any string, so this is the
#: only place the enum is enforced.
_OVERLAP_POLICIES: dict[str, OverlapPolicy] = {
    "skip": "skip",
    "queue": "queue",
    "allow": "allow",
}


class TaskDDLError(ValueError):
    """``CREATE TASK`` is syntactically valid but semantically unusable.

    Raised for a bad clause order, a duplicate clause, an unknown overlap
    policy, an invalid cron expression, or a graph cycle. The message is
    user-facing and carries no credential.
    """


@dataclass(frozen=True)
class LoweredTask:
    """A ``CREATE TASK`` statement lowered to Nova metadata.

    ``body`` is the engine-facing body for one run of the node (the ``AS``
    fragment, e.g. an ``INSERT INTO …``), matching what ``TaskSpec.body`` and
    ``execution.build_submit_task`` consume at execution time. The remaining
    fields are the normalised metadata.

    ``database_name`` / ``schema_name`` scope the task, exactly like a stage
    (``CONFIG_STAGES``): a task belongs to one ``database.schema``, and the
    explorer lists it under that schema. ``name`` is the bare task name, not the
    qualified spelling.
    """

    name: str
    body: str
    database_name: str | None = None
    schema_name: str | None = None
    schedule_kind: str = "manual"
    schedule_expr: str | None = None
    when_expr: str | None = None
    overlap_policy: OverlapPolicy = "skip"
    timezone: str | None = None
    #: Parent task names for a normal ``AFTER`` dependency, in source order.
    after: tuple[str, ...] = ()
    #: Target of a ``FINALIZE`` edge, if present.
    finalize: str | None = None
    #: ``sql`` (native one-shot), ``sql_script`` or ``python``.
    handler: str = "sql"
    #: ``sql_script``: the statements in run order, sliced from the source.
    statements: tuple[str, ...] = ()
    #: ``python``: interpreter version and ``name[==version]`` package pins.
    runtime_version: str | None = None
    packages: tuple[str, ...] = ()
    #: A ``WHEN`` over streams, compiled (``stream_when.py``); ``None`` otherwise.
    when_tree: dict | None = None

    @property
    def stream_names(self) -> tuple[str, ...]:
        """Streams the ``WHEN`` reads, as ``database.name``."""
        from app.modules.task_orchestration.stream_when import streams_in

        return tuple(dict.fromkeys(streams_in(self.when_tree))) if self.when_tree else ()

    @property
    def language(self) -> str:
        return "python" if self.handler == "python" else "sql"

    @property
    def handler_config(self) -> dict | None:
        """What the worker needs beyond ``definition``; never a credential."""
        if self.handler == "sql_script":
            return {"version": 1, "language": "sql", "statements": list(self.statements)}
        if self.handler == "python":
            return {
                "version": 1,
                "language": "python",
                "runtime_version": self.runtime_version or DEFAULT_PYTHON_RUNTIME,
                "packages": list(self.packages),
            }
        return None

    @property
    def qualified_name(self) -> str:
        """The ``database.schema.name`` spelling, for messages and graph keys."""
        parts = [p for p in (self.database_name, self.schema_name, self.name) if p]
        return ".".join(parts)


def is_create_task(sql: str) -> bool:
    """Return True when ``sql`` is Nova's ``CREATE TASK`` surface.

    Mirrors :func:`app.modules.query.dialect.ml_model.is_create_ml_model`: a
    cheap prefix test the pipeline runs before parsing, so the interception
    point does not build a parse tree just to decide. Leading whitespace and
    case are ignored.
    """
    return bool(re.match(r"^\s*CREATE\s+TASK\b", sql, re.IGNORECASE))


def _unquote_identifier(value: str) -> str:
    """Strip surrounding backticks from one qualified-name segment."""
    text = value.strip()
    if len(text) >= 2 and text.startswith("`") and text.endswith("`"):
        return text[1:-1].replace("``", "`")
    return text


def _split_qualified_task_name(
    raw: str,
) -> tuple[str, str | None, str | None]:
    """Split ``db.schema.task`` into ``(name, database, schema)``.

    The task name is the last segment; the scope is built right-to-left from the
    preceding segments, tolerating both ``db.schema.task`` and ``schema.task``.
    A four-part name (``catalog.db.schema.task``) keeps the rightmost two scope
    segments and drops the catalog, because Nova scopes tasks by
    ``database.schema`` only. Segments are split on ``.`` outside backticks so a
    quoted ``\`my.db\`.schema.task`` is not mis-split. Empty segments (a stray
    leading/trailing dot) are rejected.
    """
    segments: list[str] = []
    current: list[str] = []
    in_tick = False
    for char in raw:
        if char == "`":
            in_tick = not in_tick
            current.append(char)
        elif char == "." and not in_tick:
            segments.append("".join(current))
            current = []
        else:
            current.append(char)
    segments.append("".join(current))

    parts = [_unquote_identifier(segment) for segment in segments]
    if any(not part for part in parts):
        raise TaskDDLError(f"invalid task name {raw!r}: empty name segment")
    if len(parts) > 3:
        raise TaskDDLError(
            f"invalid task name {raw!r}: expected task, schema.task, or database.schema.task"
        )

    name = parts[-1]
    scope = parts[:-1]
    database_name = scope[0] if len(scope) >= 2 else None
    schema_name = scope[-1] if scope else None
    return name, database_name, schema_name


def parse_create_task(
    sql: str,
    *,
    database: str | None = None,
    schema: str | None = None,
    timezone: str | None = None,
    parsed=None,
) -> LoweredTask:
    """Parse ``sql`` and lower it to metadata.

    The task name is a **qualified name** — ``database.schema.task`` — exactly
    like a stage. The task belongs to that ``database.schema``; a name that
    omits the scope falls back to the session's ``database``/``schema`` (the
    same defaults the caller passes for the body). One level, two levels, or
    three, the *last* segment is the task name and the preceding segments are
    the scope, right-to-left: ``db.schema.name`` → database ``db``, schema
    ``schema``; ``schema.name`` → schema ``schema``.

    ``timezone`` is the task's IANA zone (StarRocks interprets ``START``
    literals in the session zone, so it is never assumed to be UTC by the
    caller).

    Raises :class:`TaskDDLError` for a syntax error or any validation failure
    above. No SQL literal is echoed into an error message.
    """
    if parsed is None:
        from app.sql_frontend.parser import parse_statement

        try:
            parsed = parse_statement(sql)
        except ValueError as exc:
            raise TaskDDLError("invalid CREATE TASK syntax: " + str(exc)) from None
    if [token.text.upper() for token in parsed.visible_tokens[:2]] != ["CREATE", "TASK"]:
        raise TaskDDLError("not a CREATE TASK statement")
    statement = parsed.statement_context
    if type(statement).__name__ != "SubmitTaskStatementContext":
        raise TaskDDLError("invalid CREATE TASK syntax: no task statement found")

    task_name = _slice(sql, statement.novaQualifiedTaskName())
    if not task_name:
        raise TaskDDLError("CREATE TASK requires a task name")

    # The name is qualified like a stage: `db.schema.task`. Split the scope off
    # before validation so the stored identity is (database, schema, name).
    bare_name, scoped_database, scoped_schema = _split_qualified_task_name(task_name)
    if not bare_name:
        raise TaskDDLError("CREATE TASK requires a task name")
    task_database = scoped_database or database
    task_schema = scoped_schema or schema or "default"

    clauses: dict[str, object] = {}
    for clause_ctx in _find_all(statement, "TaskClauseContext"):
        kind = _clause_kind(clause_ctx)
        if kind in clauses:
            raise TaskDDLError(f"duplicate {kind} clause")
        clauses[kind] = clause_ctx
    _reject_out_of_order(clauses)

    after = _clause_after(sql, clauses.get("AFTER"))
    finalize = _clause_finalize(sql, clauses.get("FINALIZE"))
    when_expr = _clause_when(sql, clauses.get("WHEN"))
    when_tree = _stream_when(clauses.get("WHEN"), scoped_database or database)
    overlap = _clause_overlap(sql, clauses.get("OVERLAP_POLICY"))
    schedule_kind, schedule_expr, embedded_zone = _clause_schedule(sql, clauses.get("SCHEDULE"))
    if when_tree is not None and schedule_kind == "manual" and not after and finalize is None:
        from app.modules.task_orchestration.stream_when import triggers_on_data

        if not triggers_on_data(when_tree):
            raise TaskDDLError(
                "A WHEN that is true only when streams have no data needs a SCHEDULE"
            )
        # Snowflake's triggered task: no schedule, runs when its streams have data.
        schedule_kind = "stream"
    # An explicit zone in the cron string wins: it is what the author wrote in
    # this statement, and silently preferring the session default would fire the
    # task at the wrong wall-clock moment.
    resolved_timezone = embedded_zone or timezone
    if resolved_timezone is not None:
        try:
            resolve_timezone(resolved_timezone)
        except ScheduleError as exc:
            raise TaskDDLError(f"invalid task timezone: {resolved_timezone!r}") from exc

    language = _clause_language(sql, clauses.get("LANGUAGE"))
    runtime_version = _clause_runtime_version(sql, clauses.get("RUNTIME_VERSION"))
    packages = _clause_packages(sql, clauses.get("PACKAGES"))
    if language != "python" and (runtime_version is not None or packages):
        raise TaskDDLError("RUNTIME_VERSION and PACKAGES need LANGUAGE PYTHON")

    block = _first_child(statement, "NovaTaskScriptBlockContext")
    attachment = _attachment_text(statement)
    handler = "sql"
    statements: tuple[str, ...] = ()
    if language == "python":
        if attachment is None:
            raise TaskDDLError("LANGUAGE PYTHON needs its code between $$ and $$")
        body = _python_body(attachment)
        handler = "python"
        runtime_version = runtime_version or DEFAULT_PYTHON_RUNTIME
    elif block is not None or attachment is not None:
        if block is not None:
            statements = tuple(
                _slice(sql, child)
                for child in _children(block)
                if type(child).__name__ != "TerminalNodeImpl"
            )
        else:
            statements = _split_script(attachment or "")
        statements = _validate_script(statements)
        body = ";\n".join(statements) + ";"
        handler = "sql_script"
    else:
        body = _native_body(sql, statement)

    _validate_own_edges(bare_name, after, finalize)

    return LoweredTask(
        name=bare_name,
        body=body,
        database_name=task_database,
        schema_name=task_schema,
        schedule_kind=schedule_kind,
        schedule_expr=schedule_expr,
        when_expr=when_expr,
        overlap_policy=overlap,
        timezone=resolved_timezone,
        after=after,
        finalize=finalize,
        handler=handler,
        statements=statements,
        runtime_version=runtime_version,
        packages=packages,
        when_tree=when_tree,
    )


def _stream_when(clause, database: str | None) -> dict | None:
    """The compiled ``WHEN`` when it calls a stream function, else ``None``."""
    if clause is None:
        return None
    inner = _first_child(clause, "TaskWhenClauseContext")
    if inner is None:
        return None
    from app.modules.task_orchestration.stream_when import (
        WhenError,
        compile_when,
        mentions_streams,
    )

    expression = inner.expression()
    if not mentions_streams(expression):
        return None
    try:
        return compile_when(expression, database)
    except WhenError as exc:
        raise TaskDDLError(str(exc)) from None


def _native_body(sql: str, statement) -> str:
    """The single native statement a ``sql`` task submits as ``SUBMIT TASK``."""
    body_node = (
        _find_first(statement, "CreateTableAsSelectStatementContext")
        or _find_first(statement, "InsertStatementContext")
        or _find_first(statement, "DataCacheSelectStatementContext")
    )
    if body_node is None:
        # The grammar's `AS` alternatives are CTAS / INSERT / CACHE SELECT; a
        # bare `AS SELECT` fails in the grammar before this point. Reaching here
        # means the tree shape is unfamiliar, so fail loudly rather than emit an
        # empty body to the engine.
        raise TaskDDLError("CREATE TASK body must be CREATE TABLE ... AS, INSERT, or CACHE SELECT")
    body = _slice(sql, body_node)
    from app.common.sql_guard import redact_sql_credentials

    if redact_sql_credentials(body) != body:
        raise TaskDDLError("CREATE TASK bodies must not persist credentials")
    if _find_all(body_node, "StageReferenceContext"):
        raise TaskDDLError(
            "CREATE TASK with @stage is unavailable for a single-statement task: native "
            "task definitions would persist injected storage credentials. Wrap the "
            "statement in BEGIN ... END to run it through Nova instead"
        )
    return body


def _attachment_text(statement) -> str | None:
    """The text between ``$$`` and ``$$`` when the body is a quoted script."""
    for child in _children(statement):
        if type(child).__name__ != "TerminalNodeImpl":
            continue
        text = child.getText()
        if text.startswith("$$") and text.endswith("$$") and len(text) >= 4:
            return text[2:-2]
    return None


def _split_script(text: str) -> tuple[str, ...]:
    from app.common.sql_guard import split_sql_statements

    return tuple(split_sql_statements(text))


def _validate_script(statements: tuple[str, ...]) -> tuple[str, ...]:
    """Check every statement of a script task before anything is stored.

    Each statement must parse on its own, pass the SQL guard (ACCOUNTADMIN and
    the other protected objects), carry no literal credential, and not be one
    of the statements a task may not run. Privileges are checked when the task
    runs, as the run's user, statement by statement.
    """
    from app.common.sql_guard import guard_sql, redact_sql_credentials
    from app.core.exceptions import ForbiddenSQLError
    from app.sql_frontend.parser import parse_statement

    cleaned = tuple(text.strip().rstrip(";").strip() for text in statements)
    cleaned = tuple(text for text in cleaned if text)
    if not cleaned:
        raise TaskDDLError("the task script has no statements")
    if len(cleaned) > MAX_SCRIPT_STATEMENTS:
        raise TaskDDLError(f"a task script holds at most {MAX_SCRIPT_STATEMENTS} statements")
    for index, text in enumerate(cleaned, start=1):
        where = f"statement {index}"
        try:
            parsed = parse_statement(text)
        except ValueError as exc:
            raise TaskDDLError(f"{where} of the task script is not valid SQL: {exc}") from None
        reason = _FORBIDDEN_SCRIPT_STATEMENTS.get(type(parsed.statement_context).__name__)
        if reason:
            raise TaskDDLError(f"{where}: {reason}")
        try:
            guard_sql(text)
        except ForbiddenSQLError as exc:
            raise TaskDDLError(f"{where}: {exc.message}") from None
        if redact_sql_credentials(text) != text:
            raise TaskDDLError(f"{where}: a task script must not store credentials")
    return cleaned


def _python_body(text: str) -> str:
    """Python source of a ``LANGUAGE PYTHON`` task, checked for syntax only."""
    source = text.strip("\n")
    if not source.strip():
        raise TaskDDLError("the Python task has no code")
    if len(source.encode()) > MAX_PYTHON_SOURCE_BYTES:
        raise TaskDDLError(
            f"a Python task holds at most {MAX_PYTHON_SOURCE_BYTES // 1024} KB of code"
        )
    import textwrap

    source = textwrap.dedent(source)
    try:
        compile(source, "<task>", "exec", dont_inherit=True)
    except SyntaxError as exc:
        raise TaskDDLError(
            f"Python syntax error on line {exc.lineno}: {exc.msg}"
        ) from None
    return source


def _clause_language(sql: str, clause) -> str:
    if clause is None:
        return "sql"
    inner = _first_child(clause, "TaskLanguageClauseContext")
    if inner is None:
        raise TaskDDLError("LANGUAGE clause is malformed")
    raw = _slice(sql, inner)
    value = raw[len("LANGUAGE") :].strip().lstrip("=").strip().strip("'\"").lower()
    if value not in {"sql", "python"}:
        raise TaskDDLError(f"unknown task LANGUAGE {value!r}; expected SQL or PYTHON")
    return value


def _clause_runtime_version(sql: str, clause) -> str | None:
    if clause is None:
        return None
    inner = _first_child(clause, "TaskRuntimeVersionClauseContext")
    if inner is None:
        raise TaskDDLError("RUNTIME_VERSION clause is malformed")
    value = _string_value(sql, inner.string())
    if value not in PYTHON_RUNTIME_VERSIONS:
        allowed = ", ".join(PYTHON_RUNTIME_VERSIONS)
        raise TaskDDLError(f"RUNTIME_VERSION {value!r} is not available; use {allowed}")
    return value


def _clause_packages(sql: str, clause) -> tuple[str, ...]:
    """``PACKAGES = ('name', 'name==1.2.3')`` checked against the package allowlist."""
    if clause is None:
        return ()
    inner = _first_child(clause, "TaskPackagesClauseContext")
    if inner is None:
        raise TaskDDLError("PACKAGES clause is malformed")
    from app.modules.notebooks.packages import PackageNotAllowed, canonical, validate_request

    pins: list[str] = []
    seen: set[str] = set()
    for ctx in inner.string():
        raw = _string_value(sql, ctx).strip()
        match = re.fullmatch(r"([A-Za-z0-9._-]+)\s*(?:==\s*(\S+))?", raw)
        if not match:
            raise TaskDDLError(
                f"package {raw!r} must be written name or name==version"
            )
        name, version = match.group(1), match.group(2)
        try:
            validated = validate_request(name, version)
        except PackageNotAllowed as exc:
            raise TaskDDLError(str(exc)) from None
        key = canonical(validated["name"])
        if key in seen:
            raise TaskDDLError(f"package {name!r} is listed more than once")
        seen.add(key)
        pins.append(f"{validated['name']}=={version}" if version else validated["name"])
    if len(pins) > MAX_TASK_PACKAGES:
        raise TaskDDLError(f"a Python task installs at most {MAX_TASK_PACKAGES} packages")
    return tuple(pins)


@dataclass(frozen=True)
class ExecuteTaskTarget:
    """``EXECUTE TASK [database.][schema.]name`` resolved against the session."""

    name: str
    database_name: str | None
    schema_name: str

    @property
    def qualified_name(self) -> str:
        return ".".join(p for p in (self.database_name, self.schema_name, self.name) if p)


def parse_execute_task(
    sql: str, *, parsed=None, database: str | None = None, schema: str | None = None
) -> ExecuteTaskTarget:
    """Resolve the task an ``EXECUTE TASK`` names, like ``CREATE TASK`` scopes it."""
    if parsed is None:
        from app.sql_frontend.parser import parse_statement

        try:
            parsed = parse_statement(sql)
        except ValueError as exc:
            raise TaskDDLError("invalid EXECUTE TASK syntax: " + str(exc)) from None
    statement = parsed.statement_context
    if type(statement).__name__ != "NovaExecuteTaskStatementContext":
        raise TaskDDLError("not an EXECUTE TASK statement")
    raw = _slice(sql, statement.novaQualifiedTaskName())
    name, scoped_database, scoped_schema = _split_qualified_task_name(raw)
    return ExecuteTaskTarget(
        name=name,
        database_name=scoped_database or database,
        schema_name=scoped_schema or schema or "default",
    )


@dataclass(frozen=True)
class DropTaskTarget:
    """``DROP TASK [IF EXISTS] name [FORCE]``, scoped like ``CREATE TASK``."""

    name: str
    database_name: str | None
    schema_name: str
    if_exists: bool = False
    force: bool = False

    @property
    def qualified_name(self) -> str:
        return ".".join(p for p in (self.database_name, self.schema_name, self.name) if p)


def parse_drop_task(
    sql: str, *, parsed=None, database: str | None = None, schema: str | None = None
) -> DropTaskTarget:
    """Resolve the task a ``DROP TASK`` names, with ``CREATE TASK``'s scope rule."""
    if parsed is None:
        from app.sql_frontend.parser import parse_statement

        try:
            parsed = parse_statement(sql)
        except ValueError as exc:
            raise TaskDDLError("invalid DROP TASK syntax: " + str(exc)) from None
    statement = parsed.statement_context
    if type(statement).__name__ != "DropTaskStatementContext":
        raise TaskDDLError("not a DROP TASK statement")
    name, scoped_database, scoped_schema = _split_qualified_task_name(
        _slice(sql, statement.novaQualifiedTaskName())
    )
    return DropTaskTarget(
        name=name,
        database_name=scoped_database or database,
        schema_name=scoped_schema or schema or "default",
        if_exists=statement.EXISTS() is not None,
        force=statement.FORCE() is not None,
    )


@dataclass(frozen=True)
class TaskStatementTarget:
    """ALTER TASK / DESCRIBE TASK / SHOW TASKS, scoped like ``CREATE TASK``."""

    name: str | None
    database_name: str | None
    schema_name: str | None
    action: str = ""


def parse_task_statement(
    sql: str, *, parsed, database: str | None = None, schema: str | None = None
) -> TaskStatementTarget:
    statement = parsed.statement_context
    kind = type(statement).__name__
    if kind == "NovaShowTasksStatementContext":
        node = statement.novaQualifiedTaskName()
        if node is None:
            return TaskStatementTarget(None, database, schema, "show")
        parts = [part for part in _slice(sql, node).split(".") if part]
        parts = [_unquote_identifier(part) for part in parts]
        if len(parts) > 2:
            raise TaskDDLError("SHOW TASKS IN takes a database or database.schema")
        return TaskStatementTarget(None, parts[0], parts[1] if len(parts) == 2 else None, "show")
    if kind == "NovaDescribeTaskStatementContext":
        raw = _slice(sql, statement.novaQualifiedTaskName())
        action = "describe"
    elif kind == "AlterTaskStatementContext":
        raw = _slice(sql, statement.novaQualifiedTaskName())
        action = (
            "resume" if statement.RESUME() else "suspend" if statement.SUSPEND() else "set"
        )
    else:
        raise TaskDDLError("not a task statement")
    name, scoped_database, scoped_schema = _split_qualified_task_name(raw)
    return TaskStatementTarget(
        name, scoped_database or database, scoped_schema or schema or "default", action
    )


def script_statements(task: LoweredTask) -> tuple[str, ...]:
    """The statements a script task runs, for effect analysis."""
    return task.statements if task.handler == "sql_script" else ()


def validate_merged_graph(node_names: list[str], edges: list[Edge]) -> None:
    """Validate the whole graph after a new task is merged with stored edges.

    Separate from :func:`parse_create_task` because it needs the rows that
    already exist. Raises
    :class:`~app.modules.task_orchestration.graph.GraphValidationError`, so the
    caller sees the graph layer's error type, matching the acceptance criterion.
    """
    validate_graph(Graph.from_edges(node_names, edges))


def _clause_after(sql: str, clause) -> tuple[str, ...]:
    if clause is None:
        return ()
    inner = _first_child(clause, "TaskAfterClauseContext")
    if inner is None:
        raise TaskDDLError("AFTER clause is malformed")
    names = tuple(_bare_task_ref(_slice(sql, q)) for q in _find_all(inner, "QualifiedNameContext"))
    if not names:
        raise TaskDDLError("AFTER requires at least one task name")
    if len(set(names)) != len(names):
        raise TaskDDLError("AFTER lists the same parent more than once")
    return names


def _clause_finalize(sql: str, clause) -> str | None:
    if clause is None:
        return None
    inner = _first_child(clause, "TaskFinalizeClauseContext")
    if inner is None:
        raise TaskDDLError("FINALIZE clause is malformed")
    name = _bare_task_ref(_slice(sql, inner.qualifiedName()))
    if not name:
        raise TaskDDLError("FINALIZE requires a task name")
    return name


def _bare_task_ref(raw: str) -> str:
    """A parent/finalizer reference, which must be a bare name in the schema.

    A graph is single-schema (a node cannot span schemas), so an ``AFTER`` /
    ``FINALIZE`` target is a bare task name resolved in the child's own
    ``database.schema``. A qualified reference is rejected rather than
    silently stripped: stripping would make ``AFTER other_schema.t`` resolve to
    a same-named task in this schema, which is not what the author wrote.
    """
    bare, database, schema = _split_qualified_task_name(raw)
    if database is not None or schema is not None:
        raise TaskDDLError(
            f"{raw!r} must be a bare task name; a task graph is scoped to one "
            "database.schema and parents cannot reference another schema"
        )
    return bare


def _clause_when(sql: str, clause) -> str | None:
    if clause is None:
        return None
    inner = _first_child(clause, "TaskWhenClauseContext")
    if inner is None:
        raise TaskDDLError("WHEN clause is malformed")
    expression = _slice(sql, inner.expression())
    if not expression:
        raise TaskDDLError("WHEN requires an expression")
    # The expression must be preserved verbatim, including `AND`/`OR` structure,
    # so it is sliced, never re-serialised from the tree.
    return expression


def _clause_overlap(sql: str, clause) -> OverlapPolicy:
    if clause is None:
        return "skip"
    inner = _first_child(clause, "TaskOverlapClauseContext")
    if inner is None:
        raise TaskDDLError("OVERLAP_POLICY clause is malformed")
    raw = _slice(sql, inner.identifierOrString()).strip()
    value = raw.strip("'\"")
    policy = _OVERLAP_POLICIES.get(value.lower())
    if policy is None:
        allowed = ", ".join(sorted(_OVERLAP_POLICIES))
        raise TaskDDLError(f"unknown OVERLAP_POLICY {value!r}; expected one of {allowed}")
    return policy


def _split_cron_string(raw: str) -> tuple[str, str | None]:
    """Split a Nova cron string into ``(expression, timezone | None)``.

    The surface accepts the design doc's spelling, ``'USING CRON 0 2 * * *
    Asia/Jakarta'``, as well as a bare ``'0 2 * * * Asia/Jakarta'``. The optional
    ``USING CRON`` prefix is Sugar; the optional trailing IANA zone is returned
    so the caller can honour it. A 6th token is only treated as a timezone — any
    other field count is left to :func:`parse_cron` to reject with its own
    message.
    """
    text = raw.strip()
    if text.upper().startswith("USING CRON"):
        text = text[len("USING CRON") :].strip()
    parts = text.split()
    if len(parts) == 6:
        return " ".join(parts[:5]), parts[5]
    return text, None


def _clause_schedule(sql: str, clause) -> tuple[str, str | None, str | None]:
    """Return ``(schedule_kind, schedule_expr, embedded_timezone)``.

    ``SCHEDULE = '<cron>'`` is the Nova cron form, validated through the existing
    cron parser. ``SCHEDULE START(...) EVERY(...)`` is the engine's interval
    form, mapped to ``interval``. Both parse because the grammar accepts both;
    the engine form is already in use on ``SUBMIT TASK``.
    """
    if clause is None:
        return "manual", None, None

    cron_ctx = _first_child(clause, "TaskCronScheduleDescContext")
    if cron_ctx is not None:
        raw = _string_value(sql, cron_ctx.string())
        expression, zone = _split_cron_string(raw)
        try:
            parse_cron(expression)
        except ScheduleError as exc:
            # ScheduleError messages carry the expression, which is not a
            # credential; re-raise as the surface's error type.
            raise TaskDDLError(str(exc)) from exc
        return "cron", expression, zone

    interval_ctx = _find_first(clause, "TaskIntervalContext")
    if interval_ctx is None:
        raise TaskDDLError("SCHEDULE requires a cron string or an EVERY(INTERVAL …) form")
    schedule_ctx = _find_first(clause, "TaskScheduleDescContext")
    if schedule_ctx is not None and schedule_ctx.START() is not None:
        raise TaskDDLError("CREATE TASK SCHEDULE START is not supported by the Nova scheduler")
    expression = _slice(sql, interval_ctx)
    try:
        parse_interval(expression)
    except ScheduleError as exc:
        raise TaskDDLError(str(exc)) from exc
    return "interval", expression, None


def _validate_own_edges(task_name: str, after: tuple[str, ...], finalize: str | None) -> None:
    """Reject a self-edge and validate this statement's edge shape.

    Cross-task cycles (``a AFTER b`` while ``b AFTER a``) need the whole graph,
    which the caller validates after merging with stored rows.
    """
    if task_name in after:
        raise TaskDDLError(f"task {task_name!r} cannot depend on itself (AFTER)")
    if finalize == task_name:
        raise TaskDDLError(f"task {task_name!r} cannot finalize itself")

    graph = Graph.from_edges(
        [task_name, *after, *([finalize] if finalize else [])],
        [Edge(parent=parent, child=task_name) for parent in after],
    )
    try:
        validate_graph(graph)
    except GraphValidationError as exc:  # pragma: no cover - self-cycle caught above
        raise TaskDDLError(f"invalid task graph: {exc}") from exc


def _reject_out_of_order(clauses: dict[str, object]) -> None:
    seen_rank = -1
    for kind in clauses:
        rank = CLAUSE_ORDER.index(kind)
        if rank < seen_rank:
            raise TaskDDLError(
                "CREATE TASK clauses must appear in the order " + ", ".join(CLAUSE_ORDER)
            )
        seen_rank = rank


def _clause_kind(ctx) -> str:
    """Map a ``TaskClauseContext`` to its surface keyword."""
    for child in _children(ctx):
        name = type(child).__name__
        if name == "TaskAfterClauseContext":
            return "AFTER"
        if name == "TaskFinalizeClauseContext":
            return "FINALIZE"
        if name == "TaskWhenClauseContext":
            return "WHEN"
        if name == "TaskOverlapClauseContext":
            return "OVERLAP_POLICY"
        if name in ("TaskScheduleDescContext", "TaskCronScheduleDescContext"):
            return "SCHEDULE"
        if name == "TaskLanguageClauseContext":
            return "LANGUAGE"
        if name == "TaskRuntimeVersionClauseContext":
            return "RUNTIME_VERSION"
        if name == "TaskPackagesClauseContext":
            return "PACKAGES"
    return "UNKNOWN"


def _string_value(sql: str, ctx) -> str:
    """The contents of a ``string`` context with its quotes removed."""
    return _slice(sql, ctx).strip().strip("'\"")


def _first_child(ctx, type_name: str):
    for child in _children(ctx):
        if type(child).__name__ == type_name:
            return child
    return None


def _children(ctx):
    return [ctx.getChild(i) for i in range(ctx.getChildCount())]


def _find_first(node, type_name: str):
    if type(node).__name__ == type_name:
        return node
    for i in range(node.getChildCount()):
        found = _find_first(node.getChild(i), type_name)
        if found is not None:
            return found
    return None


def _find_all(node, type_name: str) -> list:
    found: list = []
    if type(node).__name__ == type_name:
        found.append(node)
    for i in range(node.getChildCount()):
        found.extend(_find_all(node.getChild(i), type_name))
    return found


def _slice(sql: str, ctx) -> str:
    """The exact source text of ``ctx``, whitespace preserved.

    ``ctx.getText()`` concatenates tokens and loses whitespace, which silently
    corrupts a statement body. The token start/stop offsets index the original
    string, so this is the only correct way to recover text destined for the
    engine.
    """
    if ctx is None:
        return ""
    start = ctx.start.start if ctx.start is not None else 0
    stop = ctx.stop.stop if ctx.stop is not None else -1
    return sql[start : stop + 1].strip()
