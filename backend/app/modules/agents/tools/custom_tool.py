"""``custom_tool`` — run a user-defined custom tool.

Two kinds of custom tool, one runtime:

* **function** — a StarRocks FUNCTION/UDF. The call is ``SELECT fn(arg, ...)``,
  where the arguments come from the model's call. The function name is validated
  as an identifier, so a function can be stored and called without injection.
* **procedure** — a Nova-side SQL procedure: an ordered list of statements with
  named ``{{param}}`` placeholders. Parameters are substituted from the model's
  arguments, then the statements run in order as the caller. This is
  Nova's procedure layer, because StarRocks has no callable stored procedure.

Both paths go through ``QueryService`` on the requesting user's connection, so
the SQL guard, ``@stage`` handling, credential redaction, and audit all apply
exactly as they do for any other statement. The tool never opens its own socket.

Safety:

* an identifier is validated before it is interpolated; a bad name is refused;
* value parameters become escaped SQL literals; identifier parameters accept
  only plain identifiers;
* a procedure with any write statement requires per-call consent.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from app.common.audit import write_audit_log
from app.common.sql_guard import split_sql_statements
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.assistant.tools.query_execute import (
    _active_role,
    _expose_last_result,
    _render_result,
    _table_from_results,
)

logger = logging.getLogger(__name__)

#: A plain SQL identifier: a letter/underscore then letters, digits, ``_``, ``$``.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
#: ``{{name}}`` placeholder inside a procedure statement.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_PARAMETER_TYPES = {
    "string", "integer", "int", "number", "float", "decimal", "boolean",
    "date", "datetime", "array", "identifier",
}
_MAX_STATEMENTS = 32
_MAX_SQL_CHARS = 200_000
_MAX_PARAMETERS = 64


def validate_custom_tool_definition(tool: dict[str, Any]) -> list[str]:
    """Validate the model-facing contract before a custom tool is stored."""
    errors: list[str] = []
    if not str(tool.get("description") or "").strip():
        errors.append("description must say when the tool should be used")
    if not _IDENT_RE.fullmatch(str(tool.get("name") or "")):
        errors.append("name must be a plain SQL identifier")
    definition = tool.get("definition") or {}
    if not isinstance(definition, dict):
        return [*errors, "definition must be an object"]
    kind = tool.get("kind")
    if kind == "function":
        function_name = str(tool.get("function_name") or "").strip()
        if not function_name:
            errors.append("function_name is required for a function tool")
        elif not all(_IDENT_RE.fullmatch(part) for part in function_name.split(".")):
            errors.append("function_name must contain only plain identifiers")
        raw_parameters = definition.get("parameters")
        raw_args = definition.get("args")
        if (
            not raw_parameters
            and raw_args is not None
            and (
                not isinstance(raw_args, list)
                or not all(isinstance(name, str) and _IDENT_RE.match(name) for name in raw_args)
            )
        ):
            errors.append("definition.args must contain plain parameter names")
        if isinstance(raw_args, list) and len(raw_args) > _MAX_PARAMETERS:
            errors.append("definition.args exceeds the parameter limit")
    elif kind == "procedure":
        statements = definition.get("statements")
        if (
            not isinstance(statements, list)
            or not statements
            or not all(isinstance(statement, str) and statement.strip() for statement in statements)
        ):
            errors.append("procedure definition.statements must be a non-empty string list")
        elif len(statements) > _MAX_STATEMENTS or sum(map(len, statements)) > _MAX_SQL_CHARS:
            errors.append("procedure SQL exceeds the statement or size limit")
    else:
        errors.append("kind must be function or procedure")

    parameters = definition.get("parameters") or []
    if not isinstance(parameters, list):
        errors.append("definition.parameters must be a list")
        parameters = []
    if len(parameters) > _MAX_PARAMETERS:
        errors.append("definition.parameters exceeds the parameter limit")
    names: list[str] = []
    for index, parameter in enumerate(parameters):
        if not isinstance(parameter, dict):
            errors.append(f"parameter {index} must be an object")
            continue
        name = str(parameter.get("name") or "")
        if not _IDENT_RE.match(name):
            errors.append(f"parameter {index} has an invalid name")
        elif name in names:
            errors.append(f"parameter {name!r} is duplicated")
        names.append(name)
        if not str(parameter.get("description") or "").strip():
            errors.append(f"parameter {name or index!r} needs a description")
        if str(parameter.get("type") or "string").lower() not in _PARAMETER_TYPES:
            errors.append(f"parameter {name or index!r} has an unsupported type")
    if definition.get("output_mode", "result") not in {"result", "run"}:
        errors.append("output_mode must be result or run")
    if kind == "procedure" and isinstance(definition.get("statements"), list):
        placeholders = {
            match.group(1)
            for statement in definition["statements"]
            if isinstance(statement, str)
            for match in _code_placeholders(statement)
        }
        missing = sorted(placeholders - set(names))
        if missing:
            errors.append("placeholders missing parameter definitions: " + ", ".join(missing))
    return errors


def _code_placeholders(sql: str) -> list[re.Match[str]]:
    matches: list[re.Match[str]] = []
    state = "code"
    index = 0
    while index < len(sql):
        match = _PLACEHOLDER_RE.match(sql, index)
        if match is not None:
            if state == "code":
                matches.append(match)
            index = match.end()
            continue
        char = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""
        if state == "code":
            if char in {"'", '"', "`"}:
                state = char
            elif char == "-" and following == "-":
                state = "line"
                index += 1
            elif char == "#":
                state = "line"
            elif char == "/" and following == "*":
                state = "block"
                index += 1
        elif state == "line":
            if char == "\n":
                state = "code"
        elif state == "block":
            if char == "*" and following == "/":
                state = "code"
                index += 1
        elif char == "\\" and following:
            index += 1
        elif char == state:
            if following == state:
                index += 1
            else:
                state = "code"
        index += 1
    return matches


class CustomToolRunner:
    """Runs one stored custom tool on the caller's connection."""

    name = "custom_tool"
    description = (
        "Run one of the user's custom tools: a StarRocks function, or a Nova SQL "
        "procedure with parameters."
    )
    #: Dynamic per tool kind; the registry reads ``classification`` per call, so
    #: a read-only procedure may auto-approve while a writing one prompts.
    requires_consent = True

    def __init__(self, tool: dict[str, Any]) -> None:
        self.tool = tool
        self.name = f"custom_{tool.get('name', 'tool')}"
        self.description = self._description()
        self.parameters = self._parameters()
        self._classification: ToolClassification = self._classify_definition()

    @property
    def classification(self) -> ToolClassification:
        return self._classification

    def preview(self, invocation: ToolInvocation) -> str:
        args = invocation.arguments or {}
        names = ", ".join(f"{name}=<withheld>" for name in args)
        return f"custom tool `{self.tool.get('name')}`({names})"

    def classification_for(self, invocation: ToolInvocation) -> ToolClassification:
        kind = self.tool.get("kind")
        if kind == "function":
            sql = self._function_sql(invocation)
        elif kind == "procedure":
            sql = self._procedure_sql(invocation)
        else:
            return "denied"
        if isinstance(sql, ToolOutcome):
            return "denied"
        if len(sql) > _MAX_SQL_CHARS:
            return "denied"
        return self._classify_sql(sql)

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        user = getattr(context, "user", None) or {}
        username = user.get("username")
        encrypted_password = user.get("encrypted_password")
        if not username or not encrypted_password:
            return ToolOutcome(ok=False, summary="", error="No user connection is available.")

        kind = self.tool.get("kind")
        if kind == "function":
            sql = self._function_sql(invocation)
        elif kind == "procedure":
            sql = self._procedure_sql(invocation)
        else:
            return ToolOutcome(ok=False, summary="", error=f"Unsupported tool kind {kind!r}.")
        if isinstance(sql, ToolOutcome):
            return sql
        if len(sql) > _MAX_SQL_CHARS:
            return ToolOutcome(ok=False, summary="", error="The custom tool SQL is too large.")

        statements = split_sql_statements(sql)
        if not statements:
            return ToolOutcome(ok=False, summary="", error="This tool has no SQL to run.")

        from app.modules.query.sql_pipeline import guard_user_statement

        try:
            guard_user_statement(
                sql, confirm_destructive=True, allow_stage_export=True
            )
        except Exception:
            try:
                await write_audit_log(
                    event_type="query",
                    user_name=username,
                    action="custom_tool_preflight",
                    object_type="custom_tool",
                    object_name=str(self.tool.get("name") or "custom_tool"),
                    status="ERROR",
                    error_message="The custom tool SQL was blocked.",
                    session_id=getattr(context, "audit_session_id", None),
                    database_name=(
                        self.tool.get("database_name") or getattr(context, "database", None)
                    ),
                    schema_name=getattr(context, "schema_name", None),
                    active_role=_active_role(context),
                    security_context_version=user.get("security_context_version", 1),
                    decision="DENY",
                )
            except Exception as audit_exc:
                logger.warning("custom_tool preflight audit failed: %s", type(audit_exc).__name__)
            return ToolOutcome(ok=False, summary="", error="The custom tool SQL was blocked.")

        from app.modules.query.service import query_service

        try:
            results = await query_service.execute_statements(
                tenant=user.get("tenant", "default"),
                security_context_version=user.get("security_context_version", 1),
                sql=sql,
                username=username,
                encrypted_password=encrypted_password,
                database=self.tool.get("database_name") or getattr(context, "database", None),
                schema=getattr(context, "schema_name", None),
                role=_active_role(context),
                max_rows=100,
                session_id=getattr(context, "audit_session_id", None),
                confirm_destructive=True,
                allow_stage_export=True,
                file_id=getattr(context, "workspace_file_id", None),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a tool failure
            logger.warning("custom_tool failed: %s", type(exc).__name__)
            return ToolOutcome(ok=False, summary="", error="The custom tool failed.")

        failed_index = next((index for index, result in enumerate(results) if result.error), None)
        if failed_index is not None:
            return ToolOutcome(
                ok=False,
                summary="",
                error=(
                    f"Statement {failed_index + 1} failed. "
                    f"{failed_index} earlier statement(s) may have completed."
                ),
            )
        if not results:
            return ToolOutcome(ok=False, summary="", error="The custom tool returned no result.")
        # ``output_mode`` decides what the agent sees back. ``result`` returns
        # the rows; ``run`` reports only that the statements executed, so an
        # agent that just performs a write is not handed the result set.
        output_mode = (self.tool.get("definition") or {}).get("output_mode", "result")
        if output_mode == "run":
            affected = sum(getattr(r, "affected_rows", 0) or 0 for r in results)
            return ToolOutcome(ok=True, summary=f"The tool ran successfully ({affected} row(s)).")
        _expose_last_result(context, results)
        return ToolOutcome(
            ok=True,
            summary="\n".join(_render_result(result) for result in results)[:8000],
            table=_table_from_results(results),
        )

    def _description(self) -> str:
        description = str(self.tool.get("description") or "").strip()
        kind = self.tool.get("kind")
        returns = (self.tool.get("definition") or {}).get("output_mode", "result")
        suffix = (
            f" Use this only for the configured {kind}; it returns {returns}. "
            "Do not invent parameters outside its schema."
        )
        return (description or f"Run the configured Nova custom {kind} tool.") + suffix

    def _parameters(self) -> dict[str, Any]:
        definition = self.tool.get("definition") or {}
        properties: dict[str, Any] = {}
        required: list[str] = []
        raw_parameters = definition.get("parameters")
        if isinstance(raw_parameters, list) and raw_parameters:
            for item in raw_parameters:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                name = str(item["name"])
                parameter_type = str(item.get("type") or "string").lower()
                parameter_type = {
                    "int": "integer", "float": "number", "decimal": "string",
                    "date": "string", "datetime": "string", "identifier": "string",
                }.get(parameter_type, parameter_type)
                properties[name] = {
                    "type": parameter_type,
                    "description": str(item.get("description") or f"Value for {name}."),
                }
                if parameter_type == "array":
                    properties[name]["items"] = {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "number"},
                            {"type": "boolean"},
                            {"type": "null"},
                        ]
                    }
                if item.get("required", True):
                    required.append(name)
        elif self.tool.get("kind") == "function":
            for name in definition.get("args") or []:
                text = str(name)
                properties[text] = {
                    "type": "string",
                    "description": f"Argument {text} for the configured function.",
                }
                required.append(text)
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    def _classify_definition(self) -> ToolClassification:
        if self.tool.get("kind") == "function":
            return "read_only"
        statements = (self.tool.get("definition") or {}).get("statements") or []
        sql = ";\n".join(item for item in statements if isinstance(item, str))
        return self._classify_sql(sql)

    @staticmethod
    def _classify_sql(sql: str) -> ToolClassification:
        from app.modules.assistant.tools import policy

        statements = split_sql_statements(sql)
        if not statements:
            return "denied"
        classification, _ = policy.classify_statements(statements)
        return "read_only" if classification == "read_only" else "destructive"

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
        definition = self.tool.get("definition") or {}
        parameter_specs = definition.get("parameters")
        if isinstance(parameter_specs, list) and parameter_specs and not all(
            isinstance(item, dict) and isinstance(item.get("name"), str)
            for item in parameter_specs
        ):
            return ToolOutcome(ok=False, summary="", error="Invalid function parameters.")
        args = (
            [item["name"] for item in parameter_specs]
            if isinstance(parameter_specs, list) and parameter_specs
            else definition.get("args") or []
        )
        if not isinstance(args, list) or not all(isinstance(name, str) for name in args):
            return ToolOutcome(ok=False, summary="", error="Invalid function arguments.")
        optional = {
            item.get("name") for item in parameter_specs or []
            if isinstance(item, dict) and item.get("required") is False
        }
        argument_values = invocation.arguments or {}
        rendered_args: list[str] = []
        for name in args if isinstance(args, list) else []:
            if name not in argument_values:
                if name in optional:
                    rendered_args.append("NULL")
                    continue
                return ToolOutcome(
                    ok=False, summary="", error=f"Missing required parameter {name!r}."
                )
            try:
                rendered_args.append(
                    _parameter_literal(
                        argument_values[name], _parameter_type(definition, str(name))
                    )
                )
            except ValueError as exc:
                return ToolOutcome(ok=False, summary="", error=str(exc))
        qualified = ".".join(f"`{p}`" for p in raw_parts)
        return f"SELECT {qualified}({', '.join(rendered_args)})"

    def _procedure_sql(self, invocation: ToolInvocation) -> str | ToolOutcome:
        """Substitute ``{{param}}`` from the call, then join the statements."""
        definition = self.tool.get("definition") or {}
        statements = definition.get("statements") or []
        if not isinstance(statements, list) or not statements:
            return ToolOutcome(ok=False, summary="", error="This procedure has no statements.")
        if len(statements) > _MAX_STATEMENTS:
            return ToolOutcome(
                ok=False, summary="", error="This procedure has too many statements."
            )
        arguments = invocation.arguments or {}
        optional = {
            item.get("name") for item in definition.get("parameters") or []
            if isinstance(item, dict) and item.get("required") is False
        }

        def substitute(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in arguments:
                if name in optional:
                    return "NULL"
                raise KeyError(name)
            return _parameter_literal(arguments[name], _parameter_type(definition, name))

        rendered: list[str] = []
        for statement in statements:
            if not isinstance(statement, str):
                continue
            try:
                parts: list[str] = []
                cursor = 0
                for match in _code_placeholders(statement):
                    parts.append(statement[cursor : match.start()])
                    parts.append(substitute(match))
                    cursor = match.end()
                parts.append(statement[cursor:])
                rendered.append("".join(parts))
            except KeyError as exc:
                return ToolOutcome(
                    ok=False,
                    summary="",
                    error=f"Missing required parameter {exc.args[0]!r}.",
                )
            except ValueError as exc:
                return ToolOutcome(ok=False, summary="", error=str(exc))
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
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("A numeric parameter must be finite")
        return str(value)
    text = str(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{text}'"


def _parameter_type(definition: dict[str, Any], name: str) -> str:
    for item in definition.get("parameters") or []:
        if isinstance(item, dict) and item.get("name") == name:
            return str(item.get("type") or "string").lower()
    return "string"


def _parameter_literal(value: Any, kind: str) -> str:
    if kind == "identifier":
        if not isinstance(value, str) or not _IDENT_RE.fullmatch(value):
            raise ValueError("An identifier parameter must be a plain identifier")
        return value
    if kind == "array":
        return _array_literal(value)
    if kind == "boolean" and not isinstance(value, bool):
        raise ValueError("A boolean parameter must be true or false")
    if kind in {"integer", "int"} and (isinstance(value, bool) or not isinstance(value, int)):
        raise ValueError("An integer parameter must be an integer")
    if kind in {"number", "float"} and (
        isinstance(value, bool) or not isinstance(value, int | float)
    ):
        raise ValueError("A number parameter must be numeric")
    return _literal(value)


def _array_literal(value: Any, depth: int = 0) -> str:
    if not isinstance(value, list) or len(value) > 1000 or depth >= 14:
        raise ValueError("An array parameter must be a bounded list")
    values: list[str] = []
    for item in value:
        if isinstance(item, list):
            values.append(_array_literal(item, depth + 1))
        elif isinstance(item, str | int | float | bool) or item is None:
            values.append(_literal(item))
        else:
            raise ValueError("An array parameter contains an unsupported value")
    return "[" + ", ".join(values) + "]"


def _render(results: list[Any]) -> str:
    return "\n".join(_render_result(result) for result in results)[:8000]
