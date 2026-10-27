"""Migration preflight — fail fast before any statement runs.

Execute runs as the caller, so Ranger decides what the caller's active role may
create. Without a preflight, a missing privilege surfaces as a per-object
failure halfway through a run: some objects exist, some do not, and the operator
has to parse engine errors to find out why. This module names the Ranger
accesses a plan needs and reports which ones the active role is missing.

The analysis is **pure**: ``access_requests`` turns a plan into Ranger access
requests and ``analyze_access`` folds the access-control service's answers into
a result. Native StarRocks grants are never consulted; they do not authorize
anything in strict Ranger mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.access_control.domain import AccessRequest

#: The catalog migrations create their target objects in.
TARGET_CATALOG = "default_catalog"


@dataclass(frozen=True)
class RequiredAccess:
    """One privilege a plan needs and the Ranger requests that prove it."""

    privilege: str
    reason: str
    requests: tuple[AccessRequest, ...]


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


def required_privileges(
    *,
    create_database: bool,
    has_tables: bool,
    has_views: bool,
    has_materialized_views: bool,
    has_functions: bool,
    include_data: bool,
) -> list[tuple[str, str, str]]:
    """The privileges a plan needs, as ``(privilege, reason, level)``.

    ``level`` is the Ranger resource level the privilege lives on: ``catalog``,
    ``database``, or ``table``. Only privileges relevant to what the plan
    actually contains are required, so an operator is never told to grant
    something the run will not use.
    """
    required: list[tuple[str, str, str]] = []
    if create_database:
        required.append(("CREATE DATABASE", "create the target database", "catalog"))
    if has_tables:
        required.append(("CREATE TABLE", "create tables on the target", "database"))
    if has_views:
        required.append(("CREATE VIEW", "create views on the target", "database"))
    if has_materialized_views:
        required.append(
            ("CREATE MATERIALIZED VIEW", "create materialized views on the target", "database")
        )
    if has_functions:
        required.append(("CREATE FUNCTION", "create functions on the target", "database"))
    if include_data:
        required.append(("INSERT", "insert rows into the target tables", "table"))
        required.append(("SELECT", "read back the target tables for verification", "table"))
    return required


def access_requests(
    *,
    target_database: str,
    tables: list[str],
    create_database: bool,
    has_tables: bool,
    has_views: bool,
    has_materialized_views: bool,
    has_functions: bool,
    include_data: bool,
) -> list[RequiredAccess]:
    """Turn a plan into the Ranger requests each required privilege needs.

    Table-level privileges are checked on every target table, so a policy that
    covers only some of them is reported as missing.
    """
    required: list[RequiredAccess] = []
    for privilege, reason, level in required_privileges(
        create_database=create_database,
        has_tables=has_tables,
        has_views=has_views,
        has_materialized_views=has_materialized_views,
        has_functions=has_functions,
        include_data=include_data,
    ):
        access = privilege.lower()
        requests: tuple[AccessRequest, ...]
        if level == "catalog":
            requests = (AccessRequest(access=access, catalog=TARGET_CATALOG),)
        elif level == "database":
            requests = (
                AccessRequest(access=access, catalog=TARGET_CATALOG, database=target_database),
            )
        else:
            requests = tuple(
                AccessRequest(
                    access=access,
                    catalog=TARGET_CATALOG,
                    database=target_database,
                    table=table,
                )
                for table in (tables or ["*"])
            )
        required.append(RequiredAccess(privilege=privilege, reason=reason, requests=requests))
    return required


def flatten_requests(required: list[RequiredAccess]) -> list[AccessRequest]:
    return [request for item in required for request in item.requests]


def analyze_access(required: list[RequiredAccess], allowed: list[bool]) -> PreflightResult:
    """Fold per-request Ranger answers, in ``flatten_requests`` order, into checks."""
    if len(allowed) != len(flatten_requests(required)):
        raise ValueError("Ranger answers do not match the requested accesses")
    answers = iter(allowed)
    checks = tuple(
        PreflightCheck(
            privilege=item.privilege,
            reason=item.reason,
            satisfied=all([next(answers) for _ in item.requests]),
        )
        for item in required
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
