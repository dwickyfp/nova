"""Compile Nova access-control objects into Ranger policy payloads."""

from __future__ import annotations

from collections import defaultdict

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
) -> RangerPolicy:
    return RangerPolicy(
        service=settings.RANGER_SERVICE_NAME,
        name=managed_name("access", role, catalog, database, table),
        description="Managed by Nova. Object authorization is enforced by Ranger.",
        resources=table_resources(catalog, database, table, "*"),
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
