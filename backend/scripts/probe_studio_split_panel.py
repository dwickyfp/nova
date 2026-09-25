"""Run the Studio split-panel browser probe with an isolated dummy specialist.

The test account, agent, Auto runs, messages, events, and session are removed
after the browser closes. The browser token stays in subprocess memory.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
from contextlib import suppress
from pathlib import Path

from app.core.database import db
from app.core.redis import session_store
from app.core.security import create_access_token, encrypt_password
from app.modules.agents.capabilities import capability_repository
from app.modules.agents.harness_repository import harness_repository
from app.modules.agents.repository import agent_repository
from app.modules.assistant.repository import assistant_repository
from app.modules.auth.service import auth_service
from scripts.smoke_auto_two_agents import _chat_model, _sample_agent

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
MARKER = "SPLIT_PANEL_DEMO_OK"


async def _run_browser(environment: dict[str, str]) -> tuple[int, str | None, str | None]:
    process = await asyncio.create_subprocess_exec(
        "node", "scripts/probe-studio-harness-ui.mjs",
        cwd=FRONTEND, env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert process.stdout is not None
    root_id: str | None = None
    thread_id: str | None = None
    while line := await process.stdout.readline():
        output = line.decode(errors="replace").rstrip()
        print(output, flush=True)
        if match := re.fullmatch(r"Auto browser run started: ([0-9a-f-]{36})", output):
            root_id = match.group(1)
        if match := re.fullmatch(r"Thread started: ([0-9a-f-]{36})", output):
            thread_id = match.group(1)
    return await process.wait(), root_id, thread_id


async def _cleanup(
    username: str, agent_id: str | None, session_id: str | None,
    created_user: bool, role: str, created_role: bool,
    root_id: str | None, thread_id: str | None,
) -> None:
    roots = {root_id} if root_id else set()
    threads = {thread_id} if thread_id else set()
    result = await db.execute_system(
        "SELECT run_id, thread_id FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS "
        "WHERE owner_name = %s AND agent_id = '__auto__'", [username],
    )
    roots.update(str(row[0]) for row in result["rows"])
    threads.update(str(row[1]) for row in result["rows"])
    result = await db.execute_system(
        "SELECT thread_id FROM NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS "
        "WHERE user_name = %s", [username],
    )
    threads.update(str(row[0]) for row in result["rows"])
    for current_root in roots:
        with suppress(Exception):
            await harness_repository.cancel_tree(current_root)
    for current_thread in threads:
        with suppress(Exception):
            await harness_repository.delete_thread(current_thread, owner_name=username)
        await assistant_repository.delete_thread(current_thread, user_name=username)
    for current_root in roots:
        for table in ("CONFIG_AGENT_MESSAGES", "CONFIG_AGENT_SESSION_EVENTS"):
            await db.execute_system(
                f"DELETE FROM NOVA_SYSTEM.{table} WHERE root_run_id = %s",
                [current_root],
            )
    await db.execute_system(
        "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS WHERE owner_name = %s",
        [username],
    )
    for table in ("CONFIG_ASSISTANT_MESSAGES", "CONFIG_ASSISTANT_THREADS"):
        await db.execute_system(
            f"DELETE FROM NOVA_SYSTEM.{table} WHERE user_name = %s", [username]
        )
    if agent_id:
        await capability_repository.delete(agent_id, username)
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_ROLES "
            "WHERE agent_id = %s AND owner_name = %s", [agent_id, username],
        )
        await agent_repository.delete_agent(agent_id, owner_name=username)
    if session_id:
        await session_store.delete(session_id)
    if created_user:
        await db.execute_system(f"DROP USER '{username}'@'%'")
    if created_role:
        await db.execute_system(f"DROP ROLE {role}")
    checks = {
        "runs": (
            "SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS WHERE owner_name = %s",
            [username],
        ),
        "agents": (
            "SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_AGENTS WHERE owner_name = %s",
            [username],
        ),
        "threads": (
            "SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS WHERE user_name = %s",
            [username],
        ),
        "messages": (
            "SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES WHERE user_name = %s",
            [username],
        ),
    }
    counts = {}
    for attempt in range(10):
        counts = {}
        for name, (sql, params) in checks.items():
            result = await db.execute_system(sql, params)
            counts[name] = int(result["rows"][0][0])
        for current_root in roots:
            for name, table in (
                ("events", "CONFIG_AGENT_SESSION_EVENTS"),
                ("mailbox", "CONFIG_AGENT_MESSAGES"),
            ):
                result = await db.execute_system(
                    f"SELECT COUNT(*) FROM NOVA_SYSTEM.{table} WHERE root_run_id = %s",
                    [current_root],
                )
                counts[f"{name}:{current_root}"] = int(result["rows"][0][0])
        if all(count == 0 for count in counts.values()):
            break
        if attempt < 9:
            await asyncio.sleep(0.2)
    print(f"Dummy cleanup counts: {counts}", flush=True)
    assert all(count == 0 for count in counts.values()), (
        "Dummy fixture cleanup left persistent rows"
    )


async def main() -> None:
    suffix = secrets.token_hex(4)
    username = f"nova_studio_qa_{suffix}"
    role = f"studio_qa_{suffix}"
    password = secrets.token_hex(20)
    agent_id: str | None = None
    session_id: str | None = None
    created_user = False
    created_role = False
    browser_status = 1
    root_id: str | None = None
    thread_id: str | None = None
    await db.init_system_pool()
    await session_store.init()
    try:
        provider_id, model_name = await _chat_model()
        await db.execute_system(f"CREATE ROLE {role}")
        created_role = True
        await db.execute_system(
            f"CREATE USER '{username}'@'%' IDENTIFIED BY '{password}'"
        )
        created_user = True
        await db.execute_system(f"GRANT {role} TO USER '{username}'@'%'")
        assert await auth_service.verify_credentials(username, password)
        agent_id = await _sample_agent(
            username, role, provider_id, model_name,
            name="Sales Agent", domain="sales", metric="split_panel_demo_label",
            alias="split panel demo label", marker=MARKER,
        )
        session_id = await session_store.create(
            username, encrypt_password(password), [role],
            default_role=role, active_role=role,
        )
        environment = os.environ.copy()
        environment.update({
            "NOVA_STUDIO_TOKEN": create_access_token(username, session_id),
            "NOVA_STUDIO_CHILD_AGENT_ID": agent_id,
            "NOVA_STUDIO_QUESTION": (
                "Ask Sales Agent to explain the synthetic split panel demo label. "
                "Delegate this to Sales Agent and report its finding. This is dummy "
                "data with no measured business numbers."
            ),
            "NOVA_STUDIO_ANSWER_CUE": MARKER,
            "NOVA_STUDIO_EXPECT_TABLE": "0",
            "NOVA_STUDIO_STEER": (
                "Please send an intermediate finding to Main before your final answer."
            ),
        })
        print("Dummy specialist and session ready; starting browser probe", flush=True)
        browser_status, root_id, thread_id = await _run_browser(environment)
    finally:
        try:
            await _cleanup(
                username, agent_id, session_id, created_user, role, created_role,
                root_id, thread_id,
            )
        finally:
            await session_store.close()
            await db.close_system_pool()
    if browser_status:
        raise SystemExit(browser_status)


if __name__ == "__main__":
    asyncio.run(main())
