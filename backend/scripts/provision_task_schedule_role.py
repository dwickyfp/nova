"""Provision a dedicated scheduled-task account for an existing owner role.

Run with administrative engine/Ranger configuration. Random account passwords
are used only at creation; the worker delegates through its configured account.
"""

import argparse
import asyncio
import secrets

from app.common.audit import write_audit_log
from app.common.identifiers import check_identifier
from app.common.nova_system import init_task_orchestration
from app.core.config import settings
from app.core.database import db
from app.integrations.ranger.client import ranger_client
from app.integrations.ranger.schemas import RangerRole, RangerRoleMember
from app.modules.task_orchestration.repository import task_orchestration_repository as repo
from scripts.provision_task_worker_access import provision


async def bind(role_name: str, account: str, actor: str) -> None:
    check_identifier(role_name, field="owner role")
    check_identifier(account, field="scheduled execution account")
    if not account.startswith("nova_task_service_"):
        raise ValueError("Use a dedicated nova_task_service_ account")
    if role_name.lower() in {"root", "public"}:
        raise ValueError("An explicit task owner role is required")
    await init_task_orchestration()
    existing = await repo.get_role_execution_user(role_name)
    if existing and existing != account:
        raise ValueError("Role already has a different scheduled execution account")
    role = None
    if settings.RANGER_ENABLED:
        stored_role = await ranger_client.get_role(role_name)
        if not stored_role:
            raise ValueError("Owner role does not exist in Ranger")
        role = RangerRole.model_validate(stored_role)
    password = secrets.token_hex(32)
    await db.execute_system(f"CREATE USER IF NOT EXISTS '{account}' IDENTIFIED BY %s", [password])
    del password
    await db.execute_system(f"GRANT {role_name} TO USER '{account}'")
    if role is not None:
        await ranger_client.put_user_attributes(account, {})
        if not any(member.name == account for member in role.users):
            role.users.append(RangerRoleMember(name=account))
            await ranger_client.put_role(role)
        await provision(account)
    else:
        worker_role = check_identifier(settings.WORKER_IMPERSONATION_ROLE, field="worker role")
        await db.execute_system(f"GRANT IMPERSONATE ON USER '{account}'@'%' TO ROLE {worker_role}")
    await repo.bind_role_execution_user(role_name, account, actor)
    await write_audit_log(
        event_type="TASK_WORKER",
        user_name=actor,
        action="BIND_SCHEDULE_ROLE",
        object_type="ROLE",
        object_name=role_name,
        status="SUCCESS",
    )
    print(f"Scheduled tasks owned by {role_name} use {account}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--actor", required=True)
    args = parser.parse_args()
    await db.init_system_pool()
    try:
        await bind(args.role, args.account, args.actor)
    finally:
        await db.close_system_pool()


if __name__ == "__main__":
    asyncio.run(main())
