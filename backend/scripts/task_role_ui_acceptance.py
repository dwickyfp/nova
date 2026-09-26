"""Live Ranger + UI acceptance. Fixture passwords stay in process memory."""

import asyncio
import json
import secrets
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx

from app.common.audit import write_audit_log
from app.common.nova_system import init_task_orchestration
from app.core.config import settings
from app.core.database import db
from app.integrations.ranger.client import ranger_client
from app.integrations.ranger.compiler import compile_access_policy
from app.integrations.ranger.schemas import (
    RangerPolicy,
    RangerPolicyItem,
    RangerPolicyItemAccess,
    RangerPolicyResource,
    RangerRole,
    RangerRoleMember,
)
from scripts.provision_task_schedule_role import bind

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs/benchmarks/task-role-ownership-2026-09-26/main-ui"
ROLE = "NOVA_TASK_DEMO_RUNNER"
DATABASE = "NOVA_TASK_DEMO"


async def member(role_name: str, account: str) -> None:
    await ranger_client.put_user_attributes(account, {})
    raw = await ranger_client.get_role(role_name)
    role = RangerRole.model_validate(raw) if raw else RangerRole(name=role_name)
    if not any(m.name == account for m in role.users):
        role.users.append(RangerRoleMember(name=account))
    await ranger_client.put_role(role)
    await db.execute_system(f"GRANT {role_name} TO USER '{account}'")


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    env = json.loads(
        subprocess.check_output(
            ["docker", "inspect", "nova-ranger-admin", "--format", "{{json .Config.Env}}"]
        )
    )
    settings.RANGER_PASSWORD = next(
        v.split("=", 1)[1] for v in env if v.startswith("RANGER_DB_PASSWORD=")
    )
    await db.init_system_pool()
    try:
        await init_task_orchestration()
        await db.execute_system(f"CREATE ROLE IF NOT EXISTS {ROLE}")
        if not await ranger_client.get_role(ROLE):
            await ranger_client.put_role(RangerRole(name=ROLE))
        await ranger_client.put_policy(
            compile_access_policy(
                role=ROLE,
                catalog="default_catalog",
                database=DATABASE,
                table="*",
                accesses=["select", "insert"],
            )
        )
        await ranger_client.put_policy(
            RangerPolicy(
                service=settings.RANGER_SERVICE_NAME,
                name="nova-managed/task-demo/create-table",
                resources={
                    "catalog": RangerPolicyResource(values=["default_catalog"]),
                    "database": RangerPolicyResource(values=[DATABASE]),
                },
                policyItems=[
                    RangerPolicyItem(
                        roles=[ROLE], accesses=[RangerPolicyItemAccess(type="create table")]
                    )
                ],
            )
        )
        for account in ("dwicky.f.putra", "nova_admin"):
            await member(ROLE, account)
        await bind(ROLE, "nova_task_service_demo", "dwicky.f.putra")
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_TASKS SET owner_role=%s,version=version+1,updated_at=NOW() "
            "WHERE database_name=%s AND (owner_role IS NULL OR owner_role<>%s)",
            [ROLE, DATABASE, ROLE],
        )
        await write_audit_log(
            event_type="TASK_DEMO",
            user_name="dwicky.f.putra",
            active_role="ACCOUNTADMIN",
            action="ASSIGN_DEMO_ROLE",
            object_type="DATABASE",
            object_name=DATABASE,
            status="SUCCESS",
        )
        suffix = uuid4().hex[:6]
        outsider_role = f"task_outside_{suffix}"
        await db.execute_system(f"CREATE ROLE {outsider_role}")
        await ranger_client.put_role(RangerRole(name=outsider_role))
        accounts = {}
        for label, role in [("creator", ROLE), ("runner", ROLE), ("outsider", outsider_role)]:
            username = f"task_{label}_{suffix}"
            password = secrets.token_hex(24)
            await db.execute_system(f"CREATE USER '{username}' IDENTIFIED BY %s", [password])
            await member(role, username)
            await db.execute_system(f"SET DEFAULT ROLE {role} TO '{username}'@'%'")
            accounts[label] = {"username": username, "password": password}
        await write_audit_log(
            event_type="TASK_DEMO",
            user_name="dwicky.f.putra",
            action="CREATE_ROLE_TEST_FIXTURES",
            object_type="ROLE",
            object_name=ROLE,
            status="SUCCESS",
        )
        # Wait for Ranger propagation by checking actual SQL, not just API writes.
        ready = False
        for _ in range(90):
            try:
                async with (
                    db.user_conn(
                        accounts["runner"]["username"], accounts["runner"]["password"]
                    ) as conn,
                    conn.cursor() as cur,
                ):
                    await cur.execute(f"SET ROLE {ROLE}")
                    await cur.execute(f"SELECT COUNT(*) FROM {DATABASE}.source")
                    ready = (await cur.fetchone())[0] == 3
                if ready:
                    break
            except Exception:
                pass
            await asyncio.sleep(1)
        if not ready:
            raise RuntimeError("Restricted role did not propagate")
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000/api/v1", timeout=60) as client:
            login = await client.post("/auth/login", json=accounts["creator"])
            login.raise_for_status()
            token = login.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            sql = (
                f"CREATE TASK {DATABASE}.default.role_shared_{suffix} AS "
                f"INSERT INTO {DATABASE}.sample_values VALUES (2,99,'shared role')"
            )
            response = await client.post(
                "/query/execute",
                headers=headers,
                json={"sql": sql, "database": DATABASE, "schema": "default"},
            )
            response.raise_for_status()
            if not response.json()[0]["success"]:
                raise RuntimeError(response.json()[0].get("error", "CREATE TASK failed"))
        names = json.loads(
            (ROOT / "docs/benchmarks/task-acceptance-2026-09-25/main-instance.json").read_text()
        )["graph_ids"]
        tasks = [{"name": graph.rsplit(".", 1)[-1]} for graph in names]
        # Read fixtures before shared writes; UPSERT precedes its partial update.
        order = [
            "sample_values",
            "sample_copy",
            "sample_filter",
            "sample_empty",
            "sample_unicode_null",
            "sample_aggregate",
            "sample_join",
            "append",
            "upsert",
            "partial",
            "snapshot",
            "ctas",
        ]
        tasks.sort(
            key=lambda task: order.index(task["name"]) if task["name"] in order else len(order)
        )
        for task in tasks:
            if task["name"] in {"bad_parent", "bad_when"}:
                task.update(
                    expected="failed",
                    error="missing_source" if task["name"] == "bad_parent" else "missing_column",
                )
        tasks.append({"name": f"role_shared_{suffix}"})
        summary = {
            "role": ROLE,
            "database": DATABASE,
            "accounts": {key: value["username"] for key, value in accounts.items()},
            "schedule_user": "nova_task_service_demo",
        }
        (OUTPUT / "fixture.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps({"phase": "ui", **summary}), flush=True)
        payload = {
            "url": "http://localhost:5173",
            "role": ROLE,
            "output": str(OUTPUT),
            "runner": accounts["runner"],
            "outsider": accounts["outsider"],
            "tasks": tasks,
        }
        child = await asyncio.create_subprocess_exec(
            "node",
            "scripts/task-role-ui.mjs",
            cwd=str(ROOT / "frontend"),
            stdin=asyncio.subprocess.PIPE,
        )
        child.stdin.write(json.dumps(payload).encode())
        await child.stdin.drain()
        child.stdin.close()
        result = await child.wait()
        tables = {}
        for table in [
            "source",
            "sample_values",
            "sample_copy",
            "sample_filter",
            "sample_empty",
            "sample_unicode_null",
            "sample_aggregate",
            "sample_join",
            "upsert",
            "snapshot",
            "chain_c",
            "diamond_d",
            "failure",
            "finalized",
        ]:
            tables[table] = (
                await db.execute_system(f"SELECT * FROM {DATABASE}.{table} ORDER BY 1")
            )["rows"]
        (OUTPUT / "table-results.json").write_text(json.dumps(tables, indent=2, default=str))
        if result:
            raise RuntimeError("UI checks did not all pass; inspect ui-results.json")
    finally:
        await db.close_system_pool()


if __name__ == "__main__":
    asyncio.run(main())
