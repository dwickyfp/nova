"""Approved SQL writes through the caller's existing query pipeline."""

from __future__ import annotations

import re
from typing import Any

from app.common.audit import write_audit_log
from app.common.sql_guard import redact_sql_credentials, split_sql_statements
from app.modules.access_control.security_context import SecurityContext
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.assistant.tools.query_execute import _render_result, _safe_redact
from app.modules.query.sql_pipeline import guard_user_statement


def validated_write_sql(arguments: dict[str, Any]) -> str:
    sql = arguments.get("sql")
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 16000:
        raise ValueError("Provide SQL of 1 to 16000 characters.")
    statements = split_sql_statements(sql)
    if not 1 <= len(statements) <= 8:
        raise ValueError("Provide at most eight statements per approval.")
    if redact_sql_credentials(sql) != sql:
        raise ValueError("Credentials belong in protected input, never in SQL tool arguments.")
    for statement in statements:
        if re.match(r"(?:CREATE\s+USER|ALTER\s+USER|SET\s+PASSWORD)\b", statement, re.I):
            from app.modules.query.dialect.force_password_change import is_force_password_change

            if not is_force_password_change(statement):
                raise ValueError("Use provision_user for account creation with protected input.")
        if not re.match(
            r"(?:CREATE|ALTER|DROP|TRUNCATE|INSERT|UPDATE|DELETE|GRANT|REVOKE|COPY|"
            r"REFRESH|CANCEL|PAUSE|RESUME|SUBMIT|SET|SHOW|SELECT|WITH|EXPLAIN|DESC|DESCRIBE|ANALYZE)\b",
            statement, re.I,
        ):
            raise ValueError("This statement is not supported by the SQL write tool.")
        if re.match(r"SET\s+(?:DEFAULT\s+)?ROLE\b", statement, re.I):
            if re.match(r"SET\s+ROLE\b", statement, re.I):
                raise ValueError("Use query_execute for a standalone session role change.")
        guard_user_statement(statement, confirm_destructive=True)
    return sql


class QueryMutateTool:
    name = "query_mutate"
    description = (
        "Execute explicitly requested Nova SQL DDL/DML or grants on the caller's connection. "
        "Every call requires approval, including under a read-only conversation grant. "
        "Use only for execution requests, never SQL drafting. No credentials; use provision_user "
        "for account creation. A batch stops at the first failure and is not atomic."
    )
    parameters = {
        "type": "object",
        "properties": {"sql": {"type": "string"}},
        "required": ["sql"],
        "additionalProperties": False,
    }
    classification = "destructive"
    requires_consent = True

    def classification_for(self, invocation: ToolInvocation) -> str:
        try:
            validated_write_sql(invocation.arguments)
        except Exception:
            return "denied"
        return "destructive"

    def preview(self, invocation: ToolInvocation) -> str:
        return _safe_redact(str(invocation.arguments.get("sql") or ""))

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        from app.modules.query.service import query_service

        try:
            sql = validated_write_sql(invocation.arguments)
            user = context.user or {}
            security = SecurityContext.from_session(user)
            if not user.get("encrypted_password"):
                raise ValueError("No user connection is available.")
        except Exception as exc:
            return ToolOutcome(ok=False, summary="", error=_safe_redact(str(exc)),
                               error_class="POLICY_DENIED")
        audit = dict(
            event_type="assistant_tool", user_name=security.principal,
            action=self.name, object_type="SQL", object_name=context.database or "", sql_text=sql,
            session_id=context.audit_session_id, active_role=security.active_role,
        )
        try:
            await write_audit_log(**audit, status="PENDING")
        except Exception:
            return ToolOutcome(ok=False, summary="", error="The write could not be audited.")
        try:
            results = await query_service.execute_statements(
                sql=sql, username=security.principal,
                encrypted_password=user["encrypted_password"],
                role=security.active_role, database=context.database,
                schema=context.schema_name, session_id=context.audit_session_id,
                tenant=user.get("tenant", "default"),
                security_context_version=security.security_context_version,
                confirm_destructive=True, max_rows=100,
            )
        except Exception:
            try:
                await write_audit_log(**audit, status="ERROR")
            except Exception:
                pass
            return ToolOutcome(ok=False, summary="", error="SQL execution failed; inspect before retrying.")
        failed = next((r for r in results if r.error), None)
        completed = sum(1 for r in results if not r.error)
        try:
            await write_audit_log(**audit, status="ERROR" if failed or not results else "SUCCESS")
        except Exception:
            return ToolOutcome(
                ok=False, summary="",
                error=f"{completed} statements completed, but the final audit could not be recorded. Inspect before retrying.",
                error_class="AUDIT_WRITE_FAILED",
            )
        if failed or not results:
            return ToolOutcome(
                ok=False, summary="",
                error=f"{completed} statements completed before failure. "
                + _safe_redact(failed.error if failed else "No result was returned."),
                error_class="SQL_WRITE_FAILED",
            )
        return ToolOutcome(ok=True, summary="\n".join(_render_result(r) for r in results),
                           data={"statements_completed": completed, "atomic": False})


query_mutate_tool = QueryMutateTool()
