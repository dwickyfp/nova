"""Grant the configured Ranger worker impersonation for one explicit task identity."""

from __future__ import annotations

import argparse
import asyncio
import re

from app.common.audit import write_audit_log
from app.core.config import settings
from app.core.database import db
from app.integrations.ranger.client import ranger_client
from app.integrations.ranger.schemas import (
    RangerPolicy,
    RangerPolicyItem,
    RangerPolicyItemAccess,
    RangerPolicyResource,
    RangerRole,
    RangerRoleMember,
)


def scoped_policy(owner: str) -> RangerPolicy:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", owner) or owner == "root":
        raise ValueError("Specify a non-root execution account; wildcards are not allowed")
    worker = settings.WORKER_IMPERSONATION_USER
    role = settings.WORKER_IMPERSONATION_ROLE
    if not worker or worker in {"root", "nova_admin"} or not role or role == "ACCOUNTADMIN":
        raise ValueError("A dedicated worker account and role are required")
    return RangerPolicy(
        service=settings.RANGER_SERVICE_NAME,
        name=f"nova-managed/task-worker/{worker}/{owner}",
        description="Execute task runs as their persisted execution identity",
        resources={"user": RangerPolicyResource(values=[owner])},
        policyItems=[
            RangerPolicyItem(
                roles=[role],
                accesses=[RangerPolicyItemAccess(type="impersonate")],
            )
        ],
    )


async def provision(owner: str) -> None:
    policy = scoped_policy(owner)
    worker = settings.WORKER_IMPERSONATION_USER
    role_name = settings.WORKER_IMPERSONATION_ROLE
    await ranger_client.put_user_attributes(worker, {})
    existing = await ranger_client.get_role(role_name)
    role = RangerRole.model_validate(existing) if existing else RangerRole(name=role_name)
    if not any(member.name == worker for member in role.users):
        role.users.append(RangerRoleMember(name=worker))
    await ranger_client.put_role(role)
    saved = await ranger_client.put_policy(policy)
    await write_audit_log(
        event_type="TASK_WORKER",
        user_name=owner,
        action="GRANT_IMPERSONATE",
        object_type="USER",
        object_name=owner,
        status="SUCCESS",
    )
    print(f"Configured policy {saved.get('id')} for worker {worker} -> {owner}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    await db.init_system_pool()
    try:
        await provision(args.owner)
    finally:
        await db.close_system_pool()


if __name__ == "__main__":
    asyncio.run(main())
