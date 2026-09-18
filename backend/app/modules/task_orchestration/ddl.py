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

from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

from app.modules.task_orchestration.graph import (
    Edge,
    Graph,
    GraphValidationError,
    validate_graph,
)
from app.modules.task_orchestration.schedule import ScheduleError, parse_cron
from app.modules.task_orchestration.schemas import OverlapPolicy
from app.sql_dialect.grammar import StarRocksLexer, StarRocksParser

#: The surface's clause order. `taskClause*` accepts any order, so the lowering
#: enforces this one and rejects anything out of order rather than guessing.
CLAUSE_ORDER = ("AFTER", "FINALIZE", "WHEN", "OVERLAP_POLICY", "SCHEDULE")

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
    """

    name: str
    body: str
    database_name: str | None = None
    schedule_kind: str = "manual"
    schedule_expr: str | None = None
    when_expr: str | None = None
    overlap_policy: OverlapPolicy = "skip"
    timezone: str | None = None
    #: Parent task names for a normal ``AFTER`` dependency, in source order.
    after: tuple[str, ...] = ()
    #: Target of a ``FINALIZE`` edge, if present.
    finalize: str | None = None


class _TaskSyntaxErrorListener(ErrorListener):
    """Collect ANTLR syntax errors so the caller gets a Nova-shaped message."""

    def __init__(self) -> None:
        super().__init__()
        self.errors: list[str] = []

    def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):  # noqa: N802
        self.errors.append(f"line {line}:{column} {msg}")


def is_create_task(sql: str) -> bool:
    """Return True when ``sql`` is Nova's ``CREATE TASK`` surface.

    Mirrors :func:`app.modules.query.dialect.ml_model.is_create_ml_model`: a
    cheap prefix test the pipeline runs before parsing, so the interception
    point does not build a parse tree just to decide. Leading whitespace and
    case are ignored.
    """
    return bool(re.match(r"^\s*CREATE\s+TASK\b", sql, re.IGNORECASE))


def parse_create_task(
    sql: str,
    *,
    database: str | None = None,
    timezone: str | None = None,
) -> LoweredTask:
    """Parse ``sql`` and lower it to metadata.

    ``database`` is the session's default schema, used when the statement does
    not fully qualify the task name; ``timezone`` is the task's IANA zone
    (StarRocks interprets ``START`` literals in the session zone, so it is never
    assumed to be UTC by the caller).

    Raises :class:`TaskDDLError` for a syntax error or any validation failure
    above. No SQL literal is echoed into an error message.
    """
    if not is_create_task(sql):
        raise TaskDDLError("not a CREATE TASK statement")

    parser = StarRocksParser(CommonTokenStream(StarRocksLexer(InputStream(sql))))
    listener = _TaskSyntaxErrorListener()
    parser.removeErrorListeners()
    parser.addErrorListener(listener)
    tree = parser.sqlStatements()
    if listener.errors:
        raise TaskDDLError("invalid CREATE TASK syntax: " + "; ".join(listener.errors))

    statement = _find_first(tree, "SubmitTaskStatementContext")
    if statement is None:
        raise TaskDDLError("invalid CREATE TASK syntax: no task statement found")

    task_name = _slice(sql, statement.qualifiedName())
    if not task_name:
        raise TaskDDLError("CREATE TASK requires a task name")

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
    overlap = _clause_overlap(sql, clauses.get("OVERLAP_POLICY"))
    schedule_kind, schedule_expr, embedded_zone = _clause_schedule(
        sql, clauses.get("SCHEDULE")
    )
    # An explicit zone in the cron string wins: it is what the author wrote in
    # this statement, and silently preferring the session default would fire the
    # task at the wrong wall-clock moment.
    resolved_timezone = embedded_zone or timezone

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
        raise TaskDDLError(
            "CREATE TASK body must be CREATE TABLE ... AS, INSERT, or CACHE SELECT"
        )
    body = _slice(sql, body_node)

    # NOTE: `@stage` in the body cannot reach here. The pinned StarRocks grammar
    # has no `@stage` rule, so `INSERT INTO t SELECT a FROM @stage1.x` fails the
    # ANTLR parse above with `no viable alternative at input 'FROM @'` — the same
    # gap NOVA-17 tracks. That means a task body is table-only today, and the
    # worker-side `@stage` translation the design mentions is blocked on the
    # grammar work, not merely on PR 3b. Stated here so the constraint is not
    # rediscovered as a runtime surprise.

    _validate_own_edges(task_name, after, finalize)

    return LoweredTask(
        name=task_name,
        body=body,
        database_name=database,
        schedule_kind=schedule_kind,
        schedule_expr=schedule_expr,
        when_expr=when_expr,
        overlap_policy=overlap,
        timezone=resolved_timezone,
        after=after,
        finalize=finalize,
    )


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
    names = tuple(
        _slice(sql, q) for q in _find_all(inner, "QualifiedNameContext")
    )
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
    name = _slice(sql, inner.qualifiedName())
    if not name:
        raise TaskDDLError("FINALIZE requires a task name")
    return name


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
        raise TaskDDLError(
            "SCHEDULE requires a cron string or an EVERY(INTERVAL …) form"
        )
    return "interval", _slice(sql, interval_ctx), None


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
