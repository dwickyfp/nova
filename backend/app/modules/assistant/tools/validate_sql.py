"""Local syntax checks without executing SQL or resolving database objects."""

from __future__ import annotations

import re
from typing import Any

from app.common.sql_guard import redact_sql_credentials, split_sql_statements
from app.common.ml_intercept import detect_ml_forecast, detect_ml_predict, detect_ml_predict_table
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.query.dialect.force_password_change import is_force_password_change
from app.modules.query.dialect.ml_model import is_create_ml_model, parse_create_ml_model
from app.modules.query.dialect.parser import _parse_tree, parse_sql
from app.modules.query.dialect.translator import StorageConfig, translate_stage_query
from app.modules.task_orchestration.ddl import is_create_task, parse_create_task


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
    for index, stmt in enumerate(statements, 1):
        errors: list[str] = []
        dialect = "StarRocks 4.1"
        try:
            if re.match(r"CREATE\s+(?:STAGE|SEMANTIC\s+VIEW|AGENT|WAREHOUSE)\b", stmt, re.I):
                errors = ["This SQL surface is not implemented in Nova. Use supported SQL or a typed capability."]
            elif is_force_password_change(stmt):
                dialect = "Nova password-change policy"
            elif is_create_ml_model(stmt):
                parse_create_ml_model(stmt)
                dialect = "Nova ML"
            elif is_create_task(stmt):
                parse_create_task(stmt)
                dialect = "Nova task"
            elif detect_ml_forecast(stmt):
                dialect = "Nova ML forecast"
            else:
                parsed = parse_sql(stmt)
                candidate = stmt
                if parsed.stage_refs:
                    placeholder = StorageConfig("s3", "", "syntax-only", "")
                    candidate, _ = translate_stage_query(
                        parsed, {ref.stage_name: placeholder for ref in parsed.stage_refs}
                    )
                    dialect = "Nova stage"
                # parse_sql skips grammar parsing when no @stage token is present.
                _, _, syntax_errors = _parse_tree(candidate)
                errors = [f"line {e.line}, column {e.column}: {e.message}" for e in syntax_errors[:3]]
                if not errors:
                    detect_ml_predict(stmt)
                    detect_ml_predict_table(stmt)
        except ValueError as exc:
            errors = [str(exc)[:500]]
        checks.append({"statement": index, "valid": not errors, "dialect": dialect, "errors": errors})
    return checks


class ValidateSQLTool:
    name = "validate_sql"
    description = (
        "Check draft SQL against the packaged StarRocks grammar and Nova extension parsers, "
        "without executing or accessing a database. Supports '<temporary_password>' placeholders. "
        "Does not verify objects, types, permissions, runtime functions or deployment support."
    )
    parameters = {"type": "object", "properties": {"sql": {"type": "string"}},
                  "required": ["sql"], "additionalProperties": False}
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
        return ToolOutcome(ok=True, summary="Syntax checked; runtime behavior has not been verified.",
                           data={"valid": all(c["valid"] for c in checks), "statements": checks,
                                 "executed": False, "objects_verified": False})


validate_sql_tool = ValidateSQLTool()
