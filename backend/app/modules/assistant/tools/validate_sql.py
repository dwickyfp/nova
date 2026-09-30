"""Local syntax checks without executing SQL or resolving database objects."""

from __future__ import annotations

from typing import Any

from app.common.sql_guard import redact_sql_credentials, split_sql_statements
from app.modules.assistant.tools import ToolInvocation, ToolOutcome


def check_sql(sql: str) -> list[dict[str, Any]]:
    if not sql.strip() or len(sql) > 16000:
        raise ValueError("Provide SQL of 1 to 16000 characters.")
    screened = sql.replace("'<temporary_password>'", "''")
    if redact_sql_credentials(screened) != screened:
        raise ValueError("Use placeholders instead of credentials in SQL drafts.")
    statements = split_sql_statements(sql)
    if not 1 <= len(statements) <= 8:
        raise ValueError("Validate at most eight statements at once.")
    checks = []
    from app.sql_frontend.analysis.validation import validate_action
    from app.sql_frontend.ast.builder import ast_builders
    from app.sql_frontend.ast.statements import NativeStatement, StageAwareStatement
    from app.sql_frontend.parser import parse_statement

    for index, stmt in enumerate(statements, 1):
        errors: list[str] = []
        dialect = "StarRocks 4.1"
        try:
            parsed = parse_statement(stmt)
            if type(parsed.statement_context).__name__ == "CreateWarehouseStatementContext":
                raise ValueError("Nova warehouse creation is not supported")
            statement = ast_builders.build(parsed)
            if isinstance(statement, StageAwareStatement):
                dialect = "Nova stage"
            elif not isinstance(statement, NativeStatement):
                validate_action(statement)
                dialect = "Nova " + type(statement).__name__.removesuffix("Statement")
        except ValueError as exc:
            errors = [str(exc)[:500]]
        checks.append(
            {"statement": index, "valid": not errors, "dialect": dialect, "errors": errors}
        )
    return checks


class ValidateSQLTool:
    name = "validate_sql"
    description = (
        "Check draft SQL against the packaged StarRocks grammar and Nova extension parsers, "
        "without executing or accessing a database. Supports '<temporary_password>' placeholders. "
        "Does not verify objects, types, permissions, runtime functions or deployment support."
    )
    parameters = {
        "type": "object",
        "properties": {"sql": {"type": "string"}},
        "required": ["sql"],
        "additionalProperties": False,
    }
    classification = "read_only"
    requires_consent = False

    def preview(self, invocation: ToolInvocation) -> str:
        return "Check SQL syntax without execution"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        sql = invocation.arguments.get("sql")
        if not isinstance(sql, str):
            return ToolOutcome(ok=False, summary="", error="SQL is required.")
        try:
            checks = check_sql(sql)
        except Exception:
            return ToolOutcome(ok=False, summary="", error="SQL could not be checked safely.")
        return ToolOutcome(
            ok=True,
            summary="Syntax checked; runtime behavior has not been verified.",
            data={
                "valid": all(c["valid"] for c in checks),
                "statements": checks,
                "executed": False,
                "objects_verified": False,
            },
        )


validate_sql_tool = ValidateSQLTool()
