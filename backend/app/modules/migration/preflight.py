"""Migration preflight — fail fast before any statement runs.

Execute runs as the caller, so StarRocks RBAC decides what may be created. Without
a preflight, a missing privilege surfaces as a per-object failure halfway through
a run: some objects exist, some do not, and the operator has to parse engine
errors to find out why. This module reads the caller's grants first and reports
exactly which privileges a plan needs and which are missing.

The analysis is **pure**: ``analyze_grants`` takes the ``SHOW GRANTS`` rows and the
privileges a plan requires, and returns what is missing. The service reads the
grants over the caller's connection; nothing here opens one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Privileges the migration needs, grouped so the report can name the reason.
#: ``CREATE TABLE`` also covers ``CREATE TABLES`` (the engine's plural spelling).
_DB_PRIVILEGES: tuple[str, ...] = ("CREATE TABLE", "CREATE VIEW", "CREATE MATERIALIZED VIEW")
_FUNCTION_PRIVILEGE = "CREATE FUNCTION"
_DATABASE_PRIVILEGE = "CREATE DATABASE"
#: Data movement needs to write the target and read it back for verification.
_DATA_PRIVILEGES: tuple[str, ...] = ("INSERT", "SELECT")

#: ``GRANT <privs> ON <scope> TO ...`` — capture the privilege list and scope.
_GRANT = re.compile(r"^\s*GRANT\s+(?P<privs>.+?)\s+ON\s+(?P<scope>.+?)\s+TO\s", re.IGNORECASE)
#: A privilege token may carry ``WITH GRANT OPTION`` at the end of the statement.
_WITH_GRANT = re.compile(r"\s+WITH GRANT OPTION\s*$", re.IGNORECASE)


class PreflightError(ValueError):
    """A preflight request that cannot be evaluated."""


@dataclass(frozen=True)
class GrantedPrivilege:
    """One privilege from one ``SHOW GRANTS`` row, normalized."""

    privilege: str
    scope: str


@dataclass(frozen=True)
class PreflightCheck:
    """One required privilege and whether the caller holds it."""

    privilege: str
    reason: str
    satisfied: bool


@dataclass(frozen=True)
class PreflightResult:
    """The full preflight outcome."""

    checks: tuple[PreflightCheck, ...]
    storage_checked: bool = False
    storage_ok: bool | None = None
    storage_reason: str = ""

    @property
    def ok(self) -> bool:
        return all(check.satisfied for check in self.checks) and (self.storage_ok is not False)

    @property
    def missing(self) -> tuple[PreflightCheck, ...]:
        return tuple(check for check in self.checks if not check.satisfied)


def parse_grants(rows: list[tuple]) -> list[GrantedPrivilege]:
    """Normalize ``SHOW GRANTS`` rows into ``GrantedPrivilege`` entries.

    The engine emits one row per GRANT statement with the full statement text in
    the last column (``UserIdentity``, ``Catalog``, ``Grants``). Only the
    statement text matters here; the identity is the caller by definition.
    """
    granted: list[GrantedPrivilege] = []
    for row in rows:
        statement = str(row[-1]) if row else ""
        statement = _WITH_GRANT.sub("", statement.strip())
        match = _GRANT.match(statement)
        if not match:
            continue
        scope = match.group("scope").strip().lower()
        for token in match.group("privs").split(","):
            privilege = token.strip().upper()
            if privilege:
                granted.append(GrantedPrivilege(privilege=privilege, scope=scope))
    return granted


def _scope_covers(scope: str, *, catalog: bool, target_database: str) -> bool:
    """Whether a grant scope covers the operation.

    ``ALL TABLES IN ALL DATABASES`` and ``ALL DATABASES`` cover any database;
    ``DATABASE <name>`` / ``DATABASE <name>.*`` cover only that one. A
    ``CATALOG default_catalog`` scope covers the catalog-level privilege.
    """
    scope_lower = scope.lower()
    target = target_database.lower()
    if "all databases" in scope_lower:
        return True
    if catalog:
        return "catalog" in scope_lower or "default_catalog" in scope_lower
    # Database-scoped: match the named database, with or without a trailing .*
    match = re.search(r"database\s+[`\"]?(?P<name>[a-z0-9_]+)[`\"]?", scope_lower)
    if match:
        return match.group("name") == target
    # ``ALL TABLES IN DATABASE x`` is database-scoped too.
    match = re.search(r"in\s+database\s+[`\"]?(?P<name>[a-z0-9_]+)[`\"]?", scope_lower)
    if match:
        return match.group("name") == target
    return False


def _holds(
    granted: list[GrantedPrivilege],
    privilege: str,
    *,
    catalog: bool,
    target_database: str,
) -> bool:
    wanted = privilege.upper()
    for entry in granted:
        if entry.privilege != wanted:
            continue
        if _scope_covers(entry.scope, catalog=catalog, target_database=target_database):
            return True
    return False


def required_privileges(
    *,
    create_database: bool,
    has_tables: bool,
    has_views: bool,
    has_materialized_views: bool,
    has_functions: bool,
    include_data: bool,
) -> list[tuple[str, str, bool]]:
    """The privileges a plan needs, as ``(privilege, reason, is_catalog)``.

    Only privileges relevant to what the plan actually contains are required, so
    an operator is never told to grant something the run will not use.
    """
    required: list[tuple[str, str, bool]] = []
    if create_database:
        required.append((_DATABASE_PRIVILEGE, "create the target database", True))
    if has_tables:
        required.append(("CREATE TABLE", "create tables on the target", False))
    if has_views:
        required.append(("CREATE VIEW", "create views on the target", False))
    if has_materialized_views:
        required.append(
            ("CREATE MATERIALIZED VIEW", "create materialized views on the target", False)
        )
    if has_functions:
        required.append(("CREATE FUNCTION", "create functions on the target", False))
    if include_data:
        required.append(("INSERT", "insert rows into the target tables", False))
        required.append(("SELECT", "read back the target tables for verification", False))
    return required


def analyze_grants(
    *,
    granted: list[GrantedPrivilege],
    target_database: str,
    create_database: bool,
    has_tables: bool,
    has_views: bool,
    has_materialized_views: bool,
    has_functions: bool,
    include_data: bool,
) -> PreflightResult:
    """Compare the caller's grants against what the plan needs."""
    required = required_privileges(
        create_database=create_database,
        has_tables=has_tables,
        has_views=has_views,
        has_materialized_views=has_materialized_views,
        has_functions=has_functions,
        include_data=include_data,
    )
    checks = tuple(
        PreflightCheck(
            privilege=privilege,
            reason=reason,
            satisfied=_holds(
                granted,
                privilege,
                catalog=catalog,
                target_database=target_database,
            ),
        )
        for privilege, reason, catalog in required
    )
    return PreflightResult(checks=checks)


@dataclass
class PlanShape:
    """What a plan contains, so only relevant privileges are required."""

    create_database: bool = True
    has_tables: bool = False
    has_views: bool = False
    has_materialized_views: bool = False
    has_functions: bool = False
    include_data: bool = False
    tables: list[str] = field(default_factory=list)
