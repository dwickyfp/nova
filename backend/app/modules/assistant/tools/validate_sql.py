"""Syntax checks, plus a planning check of one read statement as the caller."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from app.common.sql_guard import redact_sql_credentials, split_sql_statements
from app.modules.assistant.schemas import ToolClassification
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
        "Check draft SQL against the packaged StarRocks grammar and Nova extension parsers. A "
        "single SELECT is also planned by the database as the active role (EXPLAIN, nothing "
        "runs), which checks that its tables and columns exist and may be read. Supports "
        "'<temporary_password>' placeholders."
    )
    parameters = {
        "type": "object",
        "properties": {"sql": {"type": "string"}},
        "required": ["sql"],
        "additionalProperties": False,
    }
    classification: ToolClassification = "read_only"
    requires_consent = False

    def preview(self, invocation: ToolInvocation) -> str:
        return "Check the SQL without running it"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        sql = invocation.arguments.get("sql")
        if not isinstance(sql, str):
            return ToolOutcome(ok=False, summary="", error="SQL is required.")
        try:
            checks = check_sql(sql)
        except Exception:
            return ToolOutcome(ok=False, summary="", error="SQL could not be checked safely.")
        valid = all(c["valid"] for c in checks)
        verified, problem = (
            await _plan_as_caller(sql, context) if valid and len(checks) == 1 else (False, None)
        )
        if problem:
            checks[0]["valid"] = False
            checks[0]["errors"] = [problem]
        return ToolOutcome(
            ok=True,
            summary=(
                "The database could not plan this SQL for the active role: " + problem
                if problem else
                "Syntax checked and the database planned it for the active role; nothing ran."
                if verified else
                "Syntax checked; tables and columns have not been verified."
            ),
            data={
                "valid": valid and not problem,
                "statements": checks,
                "executed": False,
                "objects_verified": verified,
            },
        )


_READ = re.compile(r"^\s*(?:SELECT|WITH)\b", re.IGNORECASE)
#: A table function as a source (FILES(...), TABLE(...), ...): planning it can
#: reach outside storage, so such SQL is never planned without consent.
_TABLE_FUNCTION = re.compile(
    r"(?:\bFROM|\bJOIN)\s*(?:LATERAL\s+)?[\w.`]+\s*\("
    # A comma-joined source inside the FROM clause, before WHERE and the rest.
    r"|(?:\bFROM|\bJOIN)\b(?:(?!\b(?:WHERE|GROUP|ORDER|HAVING|LIMIT|UNION)\b)[^;])*?,"
    r"\s*(?:LATERAL\s+)?[\w.`]+\s*\((?![^()]*\bSELECT\b)"
    r"|\b(?:FILES|TABLE)\s*\(",
    re.IGNORECASE,
)
#: A planning check is bounded; a slow catalog only means "not verified".
PLAN_TIMEOUT_SECONDS = 15.0


async def _plan_as_caller(sql: str, context: Any) -> tuple[bool, str | None]:
    """Ask the database to plan one read statement as the caller (EXPLAIN only).

    It checks that the tables and columns exist and that the active role may
    read them, without running the query or reading a row. Stage references are
    left out: planning them would resolve storage credentials.
    """
    from app.common.sql_guard import strip_sql_comments
    from app.core.exceptions import StarRocksError
    from app.modules.assistant.tools.query_execute import (
        _active_role,
        _context_value,
        _is_privilege_error,
        _safe_redact,
    )
    from app.modules.query.service import query_service

    statement = strip_sql_comments(sql).strip()
    user = getattr(context, "user", None) or {}
    if (
        not _READ.match(statement)
        or "@" in statement
        or _TABLE_FUNCTION.search(statement)
        or not user.get("username")
        or not user.get("encrypted_password")
    ):
        return False, None
    try:
        result = await asyncio.wait_for(
            query_service.explain(
                sql=statement,
                username=user["username"],
                encrypted_password=user["encrypted_password"],
                database=_context_value(context, "database"),
                schema=_context_value(context, "schema_name"),
                role=_active_role(context),
            ),
            timeout=PLAN_TIMEOUT_SECONDS,
        )
    except StarRocksError as exc:
        # The engine answered and refused the plan: that is the check's result.
        # Without an engine code the connection failed, which verifies nothing.
        if exc.engine_code is None:
            return False, None
        return True, _plan_problem(exc.message, _is_privilege_error, _safe_redact)
    except Exception:  # noqa: BLE001 - an unplanned check is "not verified", not a failure
        return False, None
    if not result.error:
        return True, None
    return True, _plan_problem(result.error, _is_privilege_error, _safe_redact)


def _plan_problem(message: str, denied: Any, redact: Any) -> str:
    if denied(message):
        return "the active role may not read one of these tables."
    return str(redact(message))[:300]


validate_sql_tool = ValidateSQLTool()
