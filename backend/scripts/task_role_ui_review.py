"""Review UI diagnostics and live access denial with disposable role members."""

import asyncio
import json
import secrets
import subprocess
from uuid import uuid4

import httpx

from app.common.audit import write_audit_log
from app.core.config import settings
from app.core.database import db
from app.integrations.ranger.client import ranger_client
from app.integrations.ranger.compiler import compile_access_policy
from app.integrations.ranger.schemas import RangerRole
from scripts.task_role_ui_acceptance import ROLE, ROOT, member


async def main() -> None:
    output = ROOT / "docs/benchmarks/task-role-ownership-2026-09-26/ui-review"
    output.mkdir(parents=True, exist_ok=True)
    env = json.loads(
        subprocess.check_output(
            ["docker", "inspect", "nova-ranger-admin", "--format", "{{json .Config.Env}}"]
        )
    )
    settings.RANGER_PASSWORD = next(
        value.split("=", 1)[1] for value in env if value.startswith("RANGER_DB_PASSWORD=")
    )
    await db.init_system_pool()
    try:
        suffix = uuid4().hex[:6]
        outside = f"task_outside_{suffix}"
        await db.execute_system(f"CREATE ROLE {outside}")
        await ranger_client.put_role(RangerRole(name=outside))
        accounts = {}
        for label, role in [("runner", ROLE), ("outsider", outside)]:
            username = f"task_review_{label}_{suffix}"
            password = secrets.token_hex(24)
            await db.execute_system(f"CREATE USER '{username}' IDENTIFIED BY %s", [password])
            await member(role, username)
            await db.execute_system(f"SET DEFAULT ROLE {role} TO '{username}'@'%'")
            accounts[label] = {"username": username, "password": password}
        await member(outside, accounts["runner"]["username"])
        negative_db = f"task_role_negative_{suffix}"
        await db.execute_system(f"CREATE DATABASE {negative_db}")
        await db.execute_system(
            f"CREATE TABLE {negative_db}.marker (id INT NOT NULL) PRIMARY KEY(id) "
            'DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES("replication_num"="1")'
        )
        await ranger_client.put_policy(
            compile_access_policy(
                role=outside,
                catalog="default_catalog",
                database=negative_db,
                table="*",
                accesses=["select", "insert"],
            )
        )
        await write_audit_log(
            event_type="TASK_DEMO",
            user_name="dwicky.f.putra",
            action="CREATE_MULTIPLE_ROLE_TEST_FIXTURES",
            object_type="DATABASE",
            object_name=negative_db,
            status="SUCCESS",
        )
        role_ready = False
        for _ in range(90):
            try:
                async with db.user_conn(
                    accounts["runner"]["username"], accounts["runner"]["password"]
                ) as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(f"SET ROLE {outside}")
                        await cur.execute(f"INSERT INTO {negative_db}.marker VALUES (99)")
                role_ready = True
                break
            except Exception:
                await asyncio.sleep(1)
        if not role_ready:
            raise RuntimeError("Alternate role's INSERT privilege did not propagate")
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000/api/v1", timeout=60) as client:

            async def authenticate(label: str) -> dict:
                for _ in range(90):
                    response = await client.post("/auth/login", json=accounts[label])
                    if response.status_code == 200:
                        return {"Authorization": f"Bearer {response.json()['access_token']}"}
                    await asyncio.sleep(1)
                raise RuntimeError("Fixture role activation failed")

            runner = await authenticate("runner")
            outsider = await authenticate("outsider")
            negative_task = f"role_denied_{suffix}"
            created = await client.post(
                "/query/execute",
                headers=runner,
                json={
                    "sql": f"CREATE TASK NOVA_TASK_DEMO.default.{negative_task} AS "
                    f"INSERT INTO {negative_db}.marker VALUES (1)",
                    "database": "NOVA_TASK_DEMO",
                    "schema": "default",
                },
            )
            created.raise_for_status()
            if not created.json()[0]["success"]:
                raise RuntimeError("Could not create role-isolation task")
            base = "/task-orchestration/graphs/NOVA_TASK_DEMO.default.chain_a"
            checks = []
            for method, path in [("GET", base), ("POST", base + "/runs")]:
                response = await client.request(method, path, headers=outsider)
                checks.append(
                    {
                        "case": f"other_role_{method}",
                        "status": response.status_code,
                        "passed": response.status_code == 404,
                    }
                )
            denied = await client.post(
                "/query/execute",
                headers=runner,
                json={
                    "sql": "SELECT * FROM NOVA_SYSTEM.CONFIG_TASK_ROLE_BINDINGS",
                    "database": "NOVA_TASK_DEMO",
                    "schema": "default",
                },
            )
            checks.append(
                {
                    "case": "restricted_role_cannot_read_system_metadata",
                    "passed": denied.status_code in (403, 404)
                    or (denied.status_code == 200 and not denied.json()[0]["success"]),
                }
            )
            payload = {
                "url": "http://localhost:5173",
                "role": ROLE,
                "output": str(output),
                "runner": accounts["runner"],
                "outsider": accounts["outsider"],
                "tasks": [
                    {"name": "sample_values"},
                    {"name": negative_task, "expected": "failed", "error": "denied"},
                ],
                "reviewErrors": [
                    {"name": "bad_parent", "error": "missing_source"},
                    {"name": "bad_when", "error": "missing_column"},
                ],
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
            ui_status = await child.wait()
            rows = await db.execute_system(f"SELECT * FROM {negative_db}.marker")
            checks.append(
                {
                    "case": "inactive_role_cannot_authorize_task_write",
                    "passed": rows["rows"] == [[99]],
                    "expected": [[99]],
                    "rows": rows["rows"],
                }
            )
            # Revoke only the disposable member, then exercise its existing session.
            account = accounts["runner"]["username"]
            raw = await ranger_client.get_role(ROLE)
            role = RangerRole.model_validate(raw)
            role.users = [user for user in role.users if user.name != account]
            await ranger_client.put_role(role)
            await db.execute_system(f"REVOKE {ROLE} FROM USER '{account}'")
            await write_audit_log(
                event_type="TASK_DEMO",
                user_name="dwicky.f.putra",
                action="REVOKE_TEST_MEMBER",
                object_type="ROLE",
                object_name=ROLE,
                status="SUCCESS",
            )
            response = await client.get(base, headers=runner)
            checks.append(
                {
                    "case": "revoked_role_rejected_with_existing_session",
                    "status": response.status_code,
                    "passed": response.status_code == 403,
                }
            )
            (output / "access-results.json").write_text(json.dumps(checks, indent=2))
            print(json.dumps({"access_checks": checks, "ui_status": ui_status}))
            if ui_status or not all(check["passed"] for check in checks):
                raise RuntimeError("Live role/UI review failed")
    finally:
        await db.close_system_pool()


if __name__ == "__main__":
    asyncio.run(main())
