"""Data governance service — dynamic masking + row access policies.

Design constraints (roadmap #3/#4, ``docs/20-data-governance.md``):

- **RBAC is StarRocks-native.** Every statement is issued on the caller's
  connection through the shared ``QueryService`` pipeline, so the engine's own
  privilege (``SECURITY`` / ``ALTER`` on the table) decides who may manage a
  policy. Nova never re-implements policy permissions, and the pipeline's guard
  (``DROP ROLE ACCOUNTADMIN`` and friends) still applies.
- **Every mutating op is audited.** The pipeline writes ``NOVA_SYSTEM.AUDIT_LOG``
  for each statement, exactly as it does for a hand-typed query.
- **Never echo a credential.** ``SHOW MASKING POLICIES`` / ``SHOW ROW ACCESS
  POLICIES`` / ``SHOW CREATE`` output is passed through the shared redactor
  before it leaves the service, and the router's response class is
  ``SanitizingJSONResponse``.

The engine's catalogue does not expose the bound-column list on all 4.1.x
builds, so the binding endpoints return the binding the caller asked for and
the list endpoints surface ``bound_columns`` / ``bound_tables`` only when the
engine reports them.
"""

from __future__ import annotations

import logging
import re

from app.common.sql_guard import split_sql_statements
from app.core.config import settings
from app.modules.access_control.service import access_control_service
from app.modules.query.service import query_service

log = logging.getLogger(__name__)

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class GovernanceError(ValueError):
    """A governance operation the service refused before or after the engine."""


def _safe_ident(value: str, what: str) -> str:
    """Return ``value`` if it is a bare identifier, else raise.

    Identifiers are interpolated into DDL; a path segment is not trusted just
    because the Pydantic model validated a create body.
    """
    if not _NAME.match(value or ""):
        raise GovernanceError(f"Invalid {what}: {value!r}")
    return value


def _safe_body(body: str) -> str:
    """Return the masking/row-access expression if it is a single statement.

    The body is embedded verbatim in the engine DDL, so a ``;`` (or a second
    statement) would let a caller add a statement the guard never saw in the
    shape it was written. Splitting is the same routine the guard uses, so this
    check and the guard agree on what a statement boundary is.
    """
    stripped = (body or "").strip()
    if not stripped:
        raise GovernanceError("Policy body must not be empty")
    if len(split_sql_statements(stripped)) != 1:
        raise GovernanceError("Policy body must be a single expression")
    return stripped


def _quote(value: str) -> str:
    """Backtick-quote an already-validated identifier."""
    return f"`{value.replace('`', '``')}`"


def _qualified(database: str, table: str) -> str:
    return f"{_quote(database)}.{_quote(table)}"


class GovernanceService:
    """Policy CRUD + bindings, run through the caller's SQL pipeline."""

    # ── Pipeline ────────────────────────────────────────────────

    async def _run(
        self,
        sql: str,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> None:
        """Execute one governance statement as the caller.

        ``confirm_destructive=True`` because ``DROP MASKING POLICY`` matches the
        destructive pattern and the API call is itself the confirmation. The hard
        guard (``guard_sql``) still runs regardless of the flag.
        """
        if settings.RANGER_ENABLED:
            raise GovernanceError(
                "Native StarRocks governance DDL is disabled in Ranger mode; "
                "use the Ranger-backed Access Control policy endpoints"
            )
        result = await query_service.execute(
            sql=sql,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
            confirm_destructive=True,
        )
        if result.error:
            raise GovernanceError(result.error)

    # ── Masking policies ────────────────────────────────────────

    async def list_masking_policies(
        self,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> list[dict]:
        if settings.RANGER_ENABLED:
            policies = await access_control_service.list_managed_policies()
            return [
                {
                    "name": str(policy.get("name", "")),
                    "body": None,
                    "bound_columns": [],
                    "bound_tables": [],
                }
                for policy in policies
                if int(policy.get("policyType", 0)) == 1
            ]
        result = await query_service.execute(
            sql="SHOW MASKING POLICIES",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        if result.error:
            raise GovernanceError(result.error)
        return _rows_to_policies(result, name_keys=("PolicyName", "Name", "policy_name"))

    async def create_masking_policy(
        self,
        *,
        name: str,
        column_type: str,
        body: str,
        comment: str | None,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        safe_name = _safe_ident(name, "policy name")
        safe_type = _safe_ident(column_type, "column type")
        safe_body = _safe_body(body)
        statement = f"CREATE MASKING POLICY {_quote(safe_name)} AS (val {safe_type}) -> {safe_body}"
        await self._run(
            statement,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return {"name": safe_name, "body": safe_body, "comment": comment}

    async def alter_masking_policy(
        self,
        name: str,
        *,
        body: str,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        safe_name = _safe_ident(name, "policy name")
        safe_body = _safe_body(body)
        await self._run(
            f"ALTER MASKING POLICY {_quote(safe_name)} SET BODY -> {safe_body}",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return {"name": safe_name, "body": safe_body}

    async def drop_masking_policy(
        self,
        name: str,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> None:
        safe_name = _safe_ident(name, "policy name")
        await self._run(
            f"DROP MASKING POLICY {_quote(safe_name)}",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )

    async def bind_masking_policy(
        self,
        *,
        database: str,
        table: str,
        column: str,
        policy_name: str | None,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        """Bind a masking policy to a column, or unbind when ``policy_name`` is null.

        StarRocks unbinds with ``MODIFY COLUMN … DROP MASKING POLICY``; the
        engine rejects an empty ``SET MASKING POLICY``. The null is translated
        here so the API keeps the shape the UI documents.
        """
        target = _qualified(
            _safe_ident(database, "database"),
            _safe_ident(table, "table"),
        )
        safe_column = _quote(_safe_ident(column, "column"))
        if policy_name is None:
            statement = f"ALTER TABLE {target} MODIFY COLUMN {safe_column} DROP MASKING POLICY"
        else:
            safe_policy = _quote(_safe_ident(policy_name, "policy name"))
            statement = (
                f"ALTER TABLE {target} MODIFY COLUMN {safe_column} SET MASKING POLICY {safe_policy}"
            )
        await self._run(
            statement,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return {
            "database": database,
            "table": table,
            "column": column,
            "policy_name": policy_name,
        }

    # ── Row access policies ─────────────────────────────────────

    async def list_row_access_policies(
        self,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> list[dict]:
        if settings.RANGER_ENABLED:
            policies = await access_control_service.list_managed_policies()
            return [
                {
                    "name": str(policy.get("name", "")),
                    "body": None,
                    "bound_columns": [],
                    "bound_tables": [],
                }
                for policy in policies
                if int(policy.get("policyType", 0)) == 2
            ]
        result = await query_service.execute(
            sql="SHOW ROW ACCESS POLICIES",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        if result.error:
            raise GovernanceError(result.error)
        return _rows_to_policies(result, name_keys=("PolicyName", "Name", "policy_name"))

    async def create_row_access_policy(
        self,
        *,
        name: str,
        argument_name: str,
        argument_type: str,
        body: str,
        comment: str | None,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        safe_name = _safe_ident(name, "policy name")
        safe_arg = _safe_ident(argument_name, "argument name")
        safe_type = _safe_ident(argument_type, "argument type")
        safe_body = _safe_body(body)
        statement = (
            f"CREATE ROW ACCESS POLICY {_quote(safe_name)} "
            f"AS ({safe_arg} {safe_type}) -> {safe_body}"
        )
        await self._run(
            statement,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return {"name": safe_name, "body": safe_body, "comment": comment}

    async def alter_row_access_policy(
        self,
        name: str,
        *,
        body: str,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        safe_name = _safe_ident(name, "policy name")
        safe_body = _safe_body(body)
        await self._run(
            f"ALTER ROW ACCESS POLICY {_quote(safe_name)} SET BODY -> {safe_body}",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return {"name": safe_name, "body": safe_body}

    async def drop_row_access_policy(
        self,
        name: str,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> None:
        safe_name = _safe_ident(name, "policy name")
        await self._run(
            f"DROP ROW ACCESS POLICY {_quote(safe_name)}",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )

    async def bind_row_access_policy(
        self,
        *,
        database: str,
        table: str,
        column: str,
        policy_name: str,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        target = _qualified(
            _safe_ident(database, "database"),
            _safe_ident(table, "table"),
        )
        safe_column = _quote(_safe_ident(column, "column"))
        safe_policy = _quote(_safe_ident(policy_name, "policy name"))
        await self._run(
            f"ALTER TABLE {target} ADD ROW ACCESS POLICY {safe_policy} ON ({safe_column})",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return {
            "database": database,
            "table": table,
            "column": column,
            "policy_name": policy_name,
        }

    async def unbind_row_access_policy(
        self,
        *,
        database: str,
        table: str,
        policy_name: str,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        target = _qualified(
            _safe_ident(database, "database"),
            _safe_ident(table, "table"),
        )
        safe_policy = _quote(_safe_ident(policy_name, "policy name"))
        await self._run(
            f"ALTER TABLE {target} DROP ROW ACCESS POLICY {safe_policy}",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return {"database": database, "table": table, "policy_name": policy_name}


def _rows_to_policies(result, *, name_keys: tuple[str, ...]) -> list[dict]:
    """Normalize a ``SHOW … POLICIES`` result into response-shaped dicts.

    The engine's column labels vary by build (``PolicyName`` in 4.1.x), so the
    mapping is by a small key set and falls back to the first column for the
    name. The body column is redacted — a policy body is user SQL, not a
    credential, but the shared redactor is the same last line of defence every
    other engine-bound string passes through.
    """
    from app.common.sql_guard import redact_sql_credentials

    columns = [str(c) for c in result.columns]
    rows = result.rows or []
    name_index = 0
    body_index: int | None = None
    for index, column in enumerate(columns):
        if column in name_keys:
            name_index = index
        if column in ("PolicyBody", "Body", "body"):
            body_index = index

    policies: list[dict] = []
    for row in rows:
        values = list(row)
        name = str(values[name_index]) if name_index < len(values) else ""
        body = None
        if body_index is not None and body_index < len(values):
            body = redact_sql_credentials(str(values[body_index]))
        policies.append({"name": name, "body": body, "bound_columns": [], "bound_tables": []})
    return policies


governance_service = GovernanceService()
