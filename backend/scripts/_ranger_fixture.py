"""Ranger-governed role fixtures for disposable live smoke scripts.

Strict Ranger mode ignores native object grants, so a fixture role needs a
StarRocks role marker for ``SET ROLE`` plus Ranger role membership and policies
for every access it exercises. Policies are named under the fixture prefix so
cleanup removes exactly what the script created.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.database import db
from app.integrations.ranger.client import ranger_client
from app.integrations.ranger.compiler import compile_access_policy, managed_name
from app.integrations.ranger.schemas import (
    RangerPolicy,
    RangerPolicyItem,
    RangerPolicyItemAccess,
    RangerPolicyResource,
    RangerRole,
    RangerRoleMember,
)

TABLE_ACCESSES = ["select", "insert", "update", "delete", "alter", "drop", "export"]
DATABASE_ACCESSES = ["create table", "create view", "create materialized view", "drop", "alter"]


async def create_role(role: str, members: list[str]) -> None:
    """Create the StarRocks marker and its Ranger role with the given members."""
    await db.execute_system(f"CREATE ROLE IF NOT EXISTS {role}")
    for member in members:
        await ranger_client.put_user_attributes(member, {})
    await ranger_client.put_role(
        RangerRole(name=role, users=[RangerRoleMember(name=member) for member in members])
    )
    for member in members:
        await db.execute_system(f"GRANT {role} TO USER '{member}'")


async def grant_database(role: str, database: str) -> list[str]:
    """Allow ``role`` to create objects in ``database`` and use its tables."""
    policies = [
        compile_access_policy(
            role=role,
            catalog="default_catalog",
            database=database,
            table="*",
            accesses=TABLE_ACCESSES,
        ),
        compile_access_policy(
            role=role,
            catalog="default_catalog",
            database=database,
            table="*",
            accesses=DATABASE_ACCESSES,
            resource_type="database",
        ),
    ]
    for policy in policies:
        await ranger_client.put_policy(policy)
    return [policy.name for policy in policies]


async def grant_impersonation(role: str, owners: list[str]) -> str:
    """Allow ``role`` to ``EXECUTE AS`` each listed owner, and nobody else."""
    policy = RangerPolicy(
        service=settings.RANGER_SERVICE_NAME,
        name=managed_name("smoke-impersonate", role),
        description="Disposable smoke fixture: execute task runs as their owners",
        resources={"user": RangerPolicyResource(values=owners)},
        policyItems=[
            RangerPolicyItem(
                roles=[role], accesses=[RangerPolicyItemAccess(type="impersonate")]
            )
        ],
    )
    await ranger_client.put_policy(policy)
    return policy.name


async def remove(*, policies: list[str], roles: list[str]) -> None:
    """Delete fixture policies first, then the Ranger roles and markers."""
    for name in policies:
        existing = await ranger_client.get_policy(name)
        if existing and existing.get("id") is not None:
            await ranger_client.delete_policy(int(existing["id"]))
    for role in roles:
        if await ranger_client.get_role(role):
            await ranger_client.delete_role(role)
        await db.execute_system(f"DROP ROLE IF EXISTS {role}")
