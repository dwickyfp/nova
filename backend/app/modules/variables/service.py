"""Variables & settings service — browse/search/paginate + ``SET`` validation.

Design constraints (roadmap #8, ``docs/22-variables-settings.md``):

- **The engine is the source of truth.** Values are read back with
  ``SHOW [GLOBAL] VARIABLES``; Nova never caches or invents a default.
- **RBAC is StarRocks-native.** ``SET GLOBAL`` runs on the caller's connection
  through the shared ``QueryService`` pipeline, so the engine's ``GLOBAL``
  privilege decides who may change a system variable and the mutation is
  audited in ``NOVA_SYSTEM.AUDIT_LOG``.
- **Search/paginate is Nova-side.** The engine's ``SHOW VARIABLES`` returns the
  full set (there is no OFFSET/LIMIT on it), so filtering and paging happen
  over the engine's rows and never over a stored copy.

Session-vs-global caveat: Nova's request pipeline opens a connection per
statement, so ``SET SESSION`` applies to that request's connection and is not
carried into later requests. ``SET GLOBAL`` is what genuinely persists. The
service returns the engine's current value for each scope and does not pretend
a session SET survived.
"""

from __future__ import annotations

import logging
import re

from app.modules.query.service import query_service

from .schemas import PasswordPolicy, VariableScope, VariableSetRequest

log = logging.getLogger(__name__)

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
#: A value that Nova renders bare. Anything else is quoted as a string literal.
_BARE_VALUE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$|^(true|false)$|^NULL$", re.IGNORECASE)

#: The password-policy variables `docs/22-variables-settings.md` documents, in
#: the order the settings page lists them.
PASSWORD_POLICY_VARIABLES: tuple[str, ...] = (
    "password_lifetime",
    "password_history",
    "failed_login_attempts",
    "password_lock_time",
    "validate_password",
    "validate_password_length",
    "validate_password_mixed_case_count",
    "validate_password_number_count",
    "validate_password_special_char_count",
)


class VariableError(ValueError):
    """A variable operation the service refused before or after the engine."""


def _safe_name(name: str) -> str:
    if not _NAME.match(name or ""):
        raise VariableError(f"Invalid variable name: {name!r}")
    return name


def _render_value(value: str) -> str:
    """Render a ``SET`` value safely: a bare numeric/bool/NULL or a quoted string.

    The value has already passed the schema's character allow-list, so it can
    never contain a quote or semicolon; this second step decides whether it is
    a number-like token or a string literal.
    """
    if _BARE_VALUE.match(value):
        return value
    escaped = value.replace("\\", "\\\\").replace("'", "''")
    return f"'{escaped}'"


def _value_to_str(value: object) -> str:
    return "" if value is None else str(value)


class VariableService:
    """Variable browse + SET through the caller's SQL pipeline."""

    async def _query(
        self,
        sql: str,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ):
        result = await query_service.execute(
            sql=sql,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        if result.error:
            raise VariableError(result.error)
        return result

    async def _run(
        self,
        sql: str,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> None:
        result = await query_service.execute(
            sql=sql,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
            confirm_destructive=True,
        )
        if result.error:
            raise VariableError(result.error)

    async def list_variables(
        self,
        *,
        scope: VariableScope,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        """Browse ``SHOW [GLOBAL] VARIABLES`` with search + pagination.

        Filtering and paging are applied to the engine's rows; the total is the
        count of matching rows, so the UI can page without a second shape.
        """
        statement = "SHOW GLOBAL VARIABLES" if scope is VariableScope.GLOBAL else "SHOW VARIABLES"
        result = await self._query(
            statement,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        items = self._rows_to_items(result, scope=scope)
        if search:
            needle = search.lower()
            items = [item for item in items if needle in item["name"].lower()]
        total = len(items)
        page = items[offset : offset + limit]
        return {
            "variables": page,
            "count": len(page),
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def set_variable(
        self,
        request: VariableSetRequest,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        """``SET [SESSION|GLOBAL] <name> = <value>`` (or ``= DEFAULT``)."""
        name = _safe_name(request.name)
        prefix = "GLOBAL " if request.scope is VariableScope.GLOBAL else "SESSION "
        if request.reset:
            rendered = "DEFAULT"
        else:
            if request.value is None:
                raise VariableError("A value is required unless reset is true")
            rendered = _render_value(request.value)
        await self._run(
            f"SET {prefix}{name} = {rendered}",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return {
            "success": True,
            "name": name,
            "scope": request.scope,
            "value": None if request.reset else rendered,
            "message": f"{name} {'reset to default' if request.reset else 'updated'}",
        }

    async def get_password_policy(
        self,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        """Read the documented password-policy variables from global scope."""
        result = await self._query(
            "SHOW GLOBAL VARIABLES",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        by_name = {item["name"].lower(): item["value"] for item in self._rows_to_items(result)}
        return {name: by_name.get(name, "") for name in PASSWORD_POLICY_VARIABLES}

    async def set_password_policy(
        self,
        policy: PasswordPolicy,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        """Write only the fields the caller supplied, each as ``SET GLOBAL``."""
        statements: list[str] = []
        for name in PASSWORD_POLICY_VARIABLES:
            value = getattr(policy, name)
            if value is None:
                continue
            rendered = "true" if value is True else "false" if value is False else str(value)
            statements.append(f"SET GLOBAL {name} = {rendered}")
        for statement in statements:
            await self._run(
                statement,
                username=username,
                encrypted_password=encrypted_password,
                session_id=session_id,
                role=role,
            )
        return {"success": True, "updated": len(statements)}

    @staticmethod
    def _rows_to_items(result, *, scope: VariableScope | None = None) -> list[dict]:
        """Normalize ``SHOW VARIABLES`` rows to ``{name, value, default}``.

        StarRocks returns two columns (``Variable_name``, ``Value``); some
        builds add a ``Default`` column. The mapping is positional-first so a
        renamed label cannot silently swap name and value.
        """
        columns = [str(c) for c in result.columns]
        name_index = 0
        value_index = 1
        default_index: int | None = None
        for index, column in enumerate(columns):
            lowered = column.lower()
            if lowered in ("variable_name", "name", "variable"):
                name_index = index
            elif lowered in ("value",):
                value_index = index
            elif lowered in ("default", "default_value"):
                default_index = index

        items: list[dict] = []
        for row in result.rows or []:
            values = list(row)
            name = _value_to_str(values[name_index]) if name_index < len(values) else ""
            value = _value_to_str(values[value_index]) if value_index < len(values) else ""
            default = (
                _value_to_str(values[default_index])
                if default_index is not None and default_index < len(values)
                else None
            )
            item = {"name": name, "value": value, "default": default}
            if scope is not None:
                item["scope"] = scope
            items.append(item)
        return items


variable_service = VariableService()
