"""Compile Nova access-control objects into Ranger policy payloads."""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from app.core.config import settings

from .schemas import (
    RangerDataMaskInfo,
    RangerDataMaskPolicyItem,
    RangerPolicy,
    RangerPolicyItem,
    RangerPolicyItemAccess,
    RangerPolicyResource,
    RangerRowFilterInfo,
    RangerRowFilterPolicyItem,
)

POLICY_TYPE_ACCESS = 0
POLICY_TYPE_DATAMASK = 1
POLICY_TYPE_ROWFILTER = 2


def managed_name(kind: str, *parts: str) -> str:
    cleaned = [part.strip().replace("/", "_") for part in parts]
    return "/".join([settings.RANGER_MANAGED_POLICY_PREFIX, kind, *cleaned])


def table_resources(
    catalog: str, database: str, table: str, column: str | None = None
) -> dict[str, RangerPolicyResource]:
    resources = {
        "catalog": RangerPolicyResource(values=[catalog]),
        "database": RangerPolicyResource(values=[database]),
        "table": RangerPolicyResource(values=[table]),
    }
    if column:
        resources["column"] = RangerPolicyResource(values=[column])
    return resources


def compile_access_policy(
    *,
    role: str,
    catalog: str,
    database: str,
    table: str,
    accesses: list[str],
    resource_type: Literal["database", "table", "materialized_view"] | None = None,
) -> RangerPolicy:
    resources = table_resources(catalog, database, table, "*")
    name = managed_name("access", role, catalog, database, table)
    if resource_type is not None:
        allowed = {
            "database": {"drop", "alter", "create table", "create view", "create function",
                         "create materialized view"},
            "table": {"drop", "insert", "update", "refresh", "delete", "export", "alter"},
            "materialized_view": {"drop", "refresh", "alter"},
        }
        if resource_type not in allowed or not accesses or any(
            access.lower() not in allowed[resource_type] for access in accesses
        ):
            raise ValueError("Unsupported access for the selected Ranger resource")
        resources = {
            "catalog": RangerPolicyResource(values=[catalog]),
            "database": RangerPolicyResource(values=[database]),
        }
        if resource_type == "database":
            if table != "*":
                raise ValueError("Database access requires the database resource scope")
        else:
            resources[resource_type] = RangerPolicyResource(values=[table])
        name = managed_name("access", resource_type, role, catalog, database, table)
    return RangerPolicy(
        service=settings.RANGER_SERVICE_NAME,
        name=name,
        description="Managed by Nova. Object authorization is enforced by Ranger.",
        resources=resources,
        policyItems=[
            RangerPolicyItem(
                roles=[role],
                accesses=[RangerPolicyItemAccess(type=value.lower()) for value in accesses],
            )
        ],
    )


def role_scope_attribute(role: str, dimension_key: str) -> str:
    return f"nova_scope.{role}.{dimension_key}"


def compile_scope_filter(*, role: str, bindings: list[tuple[str, str, list[str]]]) -> str:
    """Compile deterministic same-dimension OR and cross-dimension AND.

    Values are not embedded in SQL. Ranger substitutes the role-scoped user
    attributes at policy evaluation time. Missing attributes use a sentinel
    that cannot match a valid scope value, which is the fail-closed path.
    """
    grouped: dict[str, list[str]] = defaultdict(list)
    columns: dict[str, str] = {}
    for dimension, column, values in bindings:
        grouped[dimension].extend(values)
        columns[dimension] = column

    clauses: list[str] = []
    for dimension in sorted(grouped):
        attribute = role_scope_attribute(role, dimension)
        clauses.append(
            f"{columns[dimension]} IN "
            f"(${{{{GET_USER_ATTR_Q('{attribute}', '__nova_no_scope__')}}}})"
        )
    return " AND ".join(clauses) if clauses else "1 = 0"


def compile_row_filter_policy(
    *,
    role: str,
    catalog: str,
    database: str,
    table: str,
    bindings: list[tuple[str, str, list[str]]],
) -> RangerPolicy:
    return RangerPolicy(
        service=settings.RANGER_SERVICE_NAME,
        name=managed_name("scope", role, catalog, database, table),
        description="Managed by Nova. Missing scope attributes return no rows.",
        policyType=POLICY_TYPE_ROWFILTER,
        resources=table_resources(catalog, database, table),
        rowFilterPolicyItems=[
            RangerRowFilterPolicyItem(
                roles=[role],
                accesses=[RangerPolicyItemAccess(type="select")],
                rowFilterInfo=RangerRowFilterInfo(
                    filterExpr=compile_scope_filter(role=role, bindings=bindings)
                ),
            )
        ],
    )


def compile_mask_policy(
    *,
    role: str,
    catalog: str,
    database: str,
    table: str,
    column: str,
    mask_type: str,
    value_expression: str | None = None,
) -> RangerPolicy:
    return RangerPolicy(
        service=settings.RANGER_SERVICE_NAME,
        name=managed_name("mask", role, catalog, database, table, column),
        description="Managed by Nova. Column masking is enforced by Ranger.",
        policyType=POLICY_TYPE_DATAMASK,
        resources=table_resources(catalog, database, table, column),
        dataMaskPolicyItems=[
            RangerDataMaskPolicyItem(
                roles=[role],
                accesses=[RangerPolicyItemAccess(type="select")],
                dataMaskInfo=RangerDataMaskInfo(
                    dataMaskType=mask_type,
                    valueExpr=value_expression,
                ),
            )
        ],
    )


#: ``information_schema`` views SQL clients reflect table structure from. Rows
#: in them are filtered to objects the principal may already see.
BI_METADATA_TABLES = ("tables", "tables_config", "columns", "views", "materialized_views")


def compile_bi_metadata_policy(
    *, roles: list[str], catalog: str = "default_catalog"
) -> RangerPolicy:
    """Let BI authors reflect tables they can query (docs/arch-17-superset.md).

    Superset builds datasets from ``information_schema``. The policy grants
    ``select`` on the reflection views only; which objects appear in them is
    still decided by each principal's own object policies.
    """
    return RangerPolicy(
        service=settings.RANGER_SERVICE_NAME,
        name=managed_name("bi", "metadata", catalog),
        description="Managed by Nova. Table reflection for BI author roles.",
        resources={
            "catalog": RangerPolicyResource(values=[catalog]),
            "database": RangerPolicyResource(values=["information_schema"]),
            "table": RangerPolicyResource(values=list(BI_METADATA_TABLES)),
            "column": RangerPolicyResource(values=["*"]),
        },
        policyItems=[
            RangerPolicyItem(
                roles=sorted(set(roles)),
                accesses=[RangerPolicyItemAccess(type="select")],
            )
        ],
    )
