"""``custom_tool`` — run a user-defined custom tool.

Two kinds of custom tool, one runtime:

* **function** — a StarRocks FUNCTION/UDF. The call is ``SELECT fn(arg, ...)``,
  where the arguments come from the model's call. The function name is validated
  as an identifier, so a function can be stored and called without injection.
* **procedure** — a Nova-side SQL procedure: an ordered list of statements with
  named ``{{param}}`` placeholders. Parameters are substituted from the model's
  arguments, then the statements run in order on the caller's connection. This is
  Nova's procedure layer, because StarRocks has no callable stored procedure.

Both paths go through ``QueryService`` on the requesting user's connection, so
the SQL guard, ``@stage`` handling, credential redaction, and audit all apply
exactly as they do for any other statement. The tool never opens its own socket.

Safety:

* an identifier is validated before it is interpolated; a bad name is refused;
* a parameter value is bound as a **quoted string literal**, escaped, never
  spliced raw — a value cannot become SQL;
* a ``procedure`` is read-only unless every statement is read-only; a write
  statement makes the whole tool classify as destructive, so it prompts.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome

logger = logging.getLogger(__name__)

#: A plain SQL identifier: a letter/underscore then letters, digits, ``_``, ``$``.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
#: ``{{name}}`` placeholder inside a procedure statement.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class CustomToolRunner:
    """Runs one stored custom tool on the caller's connection."""

    name = "custom_tool"
    description = (
        "Run one of the user's custom tools: a StarRocks function, or a Nova SQL "
        "procedure with parameters."
    )
    #: Dynamic per tool kind; the registry reads ``classification`` per call, so
    #: a read-only procedure may auto-approve while a writing one prompts.
    classification: ToolClassification = "read_only"
    requires_consent = True

    def __init__(self, tool: dict[str, Any]) -> None:
        self.tool = tool
        self.name = f"custom_{tool.get('name', 'tool')}"

    def preview(self, invocation: ToolInvocation) -> str:
        args = invocation.arguments or {}
        rendered = ", ".join(f"{k}={v!r}" for k, v in args.items())
        return f"custom tool `{self.tool.get('name')}`({rendered})"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        user = getattr(context, "user", None) or {}
        username = user.get("username")
        encrypted_password = user.get("encrypted_password")
        if not username or not encrypted_password:
            return ToolOutcome(
                ok=False, summary="", error="No user connection is available."
            )

        kind = self.tool.get("kind")
        if kind == "function":
            sql = self._function_sql(invocation)
        elif kind == "procedure":
            sql = self._procedure_sql(invocation)
        else:
            return ToolOutcome(
                ok=False, summary="", error=f"Unsupported tool kind {kind!r}."
            )
        if isinstance(sql, ToolOutcome):
            return sql

        # A write statement anywhere makes the whole call destructive; the
        # policy layer reads this before consent.
        from app.common.sql_guard import split_sql_statements
        from app.modules.assistant.tools import policy

        statements = split_sql_statements(sql)
        classification, _ = policy.classify_statements(statements)
        self.classification = classification

        from app.modules.query.service import query_service

        try:
            results = await query_service.execute_statements(
                sql=sql,
                username=username,
                encrypted_password=encrypted_password,
                database=self.tool.get("database_name")
                or getattr(context, "database", None),
                schema=getattr(context, "schema_name", None),
                role=getattr(context, "role", None),
                max_rows=100,
                session_id=getattr(context, "audit_session_id", None),
                confirm_destructive=False,
                file_id=getattr(context, "workspace_file_id", None),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a tool failure
            logger.warning("custom_tool failed: %s", type(exc).__name__)
            return ToolOutcome(ok=False, summary="", error="The custom tool failed.")

        failed = next((r for r in results if r.error), None)
        if failed is not None:
            return ToolOutcome(
                ok=False, summary="", error="The custom tool returned an error."
            )
        # ``output_mode`` decides what the agent sees back. ``result`` returns
        # the rows; ``run`` reports only that the statements executed, so an
        # agent that just performs a write is not handed the result set.
        output_mode = (self.tool.get("definition") or {}).get("output_mode", "result")
        if output_mode == "run":
            affected = sum(
                getattr(r, "row_count", 0) or 0 for r in results
            )
            return ToolOutcome(
                ok=True, summary=f"The tool ran successfully ({affected} row(s))."
            )
        return ToolOutcome(ok=True, summary=_render(results))

    def _function_sql(self, invocation: ToolInvocation) -> str | ToolOutcome:
        """``SELECT db.fn(arg, ...)`` with identifier-validated names.

        The database comes from the tool's ``database_name``; the function name
        may itself be qualified (in which case it is used as given). Every part
        must be a plain identifier, so a name cannot carry SQL.
        """
        function_name = (self.tool.get("function_name") or "").strip()
        if not function_name:
            return ToolOutcome(
                ok=False, summary="", error="This function tool has no function name."
            )
        database = (self.tool.get("database_name") or "").strip()
        raw_parts = function_name.split(".")
        if database and len(raw_parts) == 1:
            raw_parts = [database, *raw_parts]
        for part in raw_parts:
            if not _IDENT_RE.match(part):
                return ToolOutcome(
                    ok=False, summary="", error=f"Unsafe function name {function_name!r}."
                )
        args = self.tool.get("definition", {}).get("args") or []
        argument_values = invocation.arguments or {}
        rendered_args: list[str] = []
        for name in args if isinstance(args, list) else []:
            rendered_args.append(_literal(argument_values.get(str(name))))
        qualified = ".".join(f"`{p}`" for p in raw_parts)
        return f"SELECT {qualified}({', '.join(rendered_args)})"

    def _procedure_sql(self, invocation: ToolInvocation) -> str | ToolOutcome:
        """Substitute ``{{param}}`` from the call, then join the statements."""
        definition = self.tool.get("definition") or {}
        statements = definition.get("statements") or []
        if not isinstance(statements, list) or not statements:
            return ToolOutcome(
                ok=False, summary="", error="This procedure has no statements."
            )
        arguments = invocation.arguments or {}

        def substitute(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in arguments:
                raise KeyError(name)
            return _literal(arguments[name])

        rendered: list[str] = []
        for statement in statements:
            if not isinstance(statement, str):
                continue
            try:
                rendered.append(_PLACEHOLDER_RE.sub(substitute, statement))
            except KeyError as exc:
                return ToolOutcome(
                    ok=False,
                    summary="",
                    error=f"Missing required parameter {exc.args[0]!r}.",
                )
        return ";\n".join(rendered) + ";"


def _literal(value: Any) -> str:
    """Render a value as a SQL literal, safely.

    A value is never spliced as SQL: strings are quoted with escaped quotes,
    numbers pass through as numbers, booleans and null are keywords. Everything
    else is stringified and quoted.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{text}'"


def _render(results: list[Any]) -> str:
    lines: list[str] = []
    for result in results:
        columns = list(getattr(result, "columns", []) or [])
        rows = list(getattr(result, "rows", []) or [])
        if columns:
            lines.append("columns: " + ", ".join(columns))
        lines.append(f"row_count: {getattr(result, 'row_count', len(rows))}")
        for row in rows[:50]:
            lines.append(" | ".join("" if v is None else str(v) for v in row))
    return "\n".join(lines) or "The tool ran and returned no rows."
