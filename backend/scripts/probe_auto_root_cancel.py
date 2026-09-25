"""Cancel a live Auto root while its synthetic specialist is running.

Run from backend while dev.sh serves the API and agent worker::

    STARROCKS_FE_MYSQL_PORT=29030 RANGER_ENABLED=true \
      .venv/bin/python -m scripts.probe_auto_root_cancel
"""

from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import suppress

import httpx

from app.core.database import db
from app.core.redis import session_store
from app.core.security import create_access_token, encrypt_password
from app.modules.agents.capabilities import capability_repository
from app.modules.agents.harness_repository import TERMINAL, harness_repository
from app.modules.agents.repository import agent_repository
from app.modules.assistant.repository import assistant_repository
from app.modules.auth.service import auth_service
from scripts.smoke_auto_two_agents import BASE_URL, _chat_model, _request, _sample_agent


async def _sse_replay(
    client: httpx.AsyncClient, thread_id: str, root_id: str, *, after: int = -1,
) -> list[tuple[str, dict]]:
    url = f"{BASE_URL}/__auto__/threads/{thread_id}/runs/{root_id}/events"
    frames: list[tuple[str, dict]] = []
    kind = ""
    payload: dict = {}
    async with asyncio.timeout(30):
        async with client.stream("GET", url, params={"after": after}) as response:
            assert response.status_code == 200, f"Replay returned {response.status_code}"
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    kind = line[7:]
                elif line.startswith("data: "):
                    payload = json.loads(line[6:])
                elif not line and kind:
                    if kind != "ping":
                        frames.append((kind, payload))
                    if kind == "done":
                        break
                    kind = ""
                    payload = {}
    assert frames and frames[-1][0] == "done", "Replay did not terminate"
    return frames


async def _count(table: str, clause: str, params: list[str]) -> int:
    result = await db.execute_system(
        f"SELECT COUNT(*) FROM NOVA_SYSTEM.{table} WHERE {clause}", params
    )
    return int(result["rows"][0][0])


async def main() -> None:
    suffix = secrets.token_hex(4)
    username = f"nova_cancel_probe_{suffix}"
    role = f"cancel_probe_{suffix}"
    password = secrets.token_hex(20)
    thread_id: str | None = None
    root_id: str | None = None
    child_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    created_user = False
    created_role = False
    succeeded = False
    cleanup_errors: list[str] = []
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
            name="Auto Cancel Probe", domain="cancellation",
            metric="cancel_demo_label", alias="cancel demo label",
            marker="CANCEL_DEMO_OK",
        )
        session_id = await session_store.create(
            username, encrypt_password(password), [role],
            default_role=role, active_role=role,
        )
        token = create_access_token(username, session_id)
        async with httpx.AsyncClient(
            timeout=20, headers={"Authorization": f"Bearer {token}"}
        ) as client:
            discovered = await _request(
                client, "GET", f"{BASE_URL}/capabilities",
                params={"query": "cancel demo label"},
            )
            assert agent_id in {item["agent_id"] for item in discovered["agents"]}
            thread = await _request(
                client, "POST", f"{BASE_URL}/__auto__/threads",
                json={"title": "Live root cancellation probe"},
            )
            thread_id = thread["thread_id"]
            async with client.stream(
                "POST", f"{BASE_URL}/__auto__/threads/{thread_id}/messages",
                json={
                    "content": (
                        "Synthetic harness cancellation test. Delegate to the available "
                        "specialist to explain cancel demo label and CANCEL_DEMO_OK. "
                        "No business data, measurements, or calculations are involved."
                    ),
                    "provider_id": provider_id,
                    "model": model_name,
                },
            ) as response:
                assert response.status_code == 200, response.status_code
                root_id = response.headers.get("X-Nova-Run-ID")
                assert root_id, "Auto turn did not expose a root run ID"

            deadline = asyncio.get_running_loop().time() + 150
            while asyncio.get_running_loop().time() < deadline:
                try:
                    tree = await _request(client, "GET", f"{BASE_URL}/auto/runs/{root_id}")
                except RuntimeError as exc:
                    if "HTTP 404" not in str(exc):
                        raise
                    await asyncio.sleep(0.1)
                    continue
                root = next(run for run in tree["runs"] if run["depth"] == 0)
                children = [run for run in tree["runs"] if run["depth"] == 1]
                if children:
                    child = children[0]
                    child_id = child["run_id"]
                    if child["status"] in TERMINAL or root["status"] in TERMINAL:
                        raise AssertionError(
                            f"Child finished before cancellation: "
                            f"root={root['status']} child={child['status']}"
                        )
                    print("status at cancellation:", root["status"], child["status"])
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("Auto did not start a specialist in 150 seconds")

            assert child_id
            try:
                cancelled = await _request(
                    client, "POST", f"{BASE_URL}/auto/runs/{root_id}/cancel"
                )
            except RuntimeError:
                current = await _request(
                    client, "GET", f"{BASE_URL}/auto/runs/{root_id}"
                )
                print("status after rejected cancellation:", [
                    (run["run_id"], run["status"]) for run in current["runs"]
                ])
                journal = await db.execute_system(
                    "SELECT session_sequence, run_id, event_type "
                    "FROM NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS "
                    "WHERE root_run_id = %s ORDER BY session_sequence",
                    [root_id],
                )
                print("journal after rejected cancellation:", journal["rows"])
                raise
            assert cancelled["status"] == "cancelled"
            tree = await _request(client, "GET", f"{BASE_URL}/auto/runs/{root_id}")
            status_by_id = {run["run_id"]: run["status"] for run in tree["runs"]}
            assert status_by_id[root_id] == "cancelled", status_by_id
            assert status_by_id[child_id] == "cancelled", status_by_id
            persisted_root = await harness_repository.get(root_id)
            persisted_child = await harness_repository.get(child_id)
            assert persisted_root and persisted_root["status"] == "cancelled"
            assert persisted_child and persisted_child["status"] == "cancelled"

            events = await _request(client, "GET", f"{BASE_URL}/auto/runs/{root_id}/events")
            child_terminal = [
                int(item["event_id"]) for item in events["events"]
                if item["run_id"] == child_id and item["type"] == "agent_cancelled"
            ]
            root_terminal = [
                int(item["event_id"]) for item in events["events"]
                if item["run_id"] == root_id and item["type"] == "agent_cancelled"
            ]
            assert len(child_terminal) == len(root_terminal) == 1
            assert child_terminal[0] < root_terminal[0], (
                child_terminal, root_terminal
            )
            timeline = await _request(
                client, "GET",
                f"{BASE_URL}/auto/runs/{root_id}/children/{child_id}/timeline",
            )
            assert timeline["run"]["status"] == "cancelled"
            assert any(item["type"] == "agent_cancelled" for item in timeline["events"])

            replay = await _sse_replay(client, thread_id, root_id)
            kinds = [kind for kind, _ in replay]
            assert kinds[-3:] == ["agent_cancelled", "error", "done"], kinds
            assert replay[-2][1]["code"] == "agent_cancelled"
            assert replay[-1][1]["finish_reason"] == "error"
            terminal_sequence = replay[-3][1]["sequence"]
            assert [kind for kind, _ in await _sse_replay(
                client, thread_id, root_id, after=terminal_sequence
            )] == ["error", "done"]
            thread = await _request(
                client, "GET", f"{BASE_URL}/__auto__/threads/{thread_id}"
            )
            assert not any(
                item["role"] == "assistant" for item in thread["messages"]
            ), "Cancelled Auto wrote an assistant final answer"
            print("PASS live root cancellation with child-before-root terminal order")
            print("root run:", root_id)
            print("child run:", child_id)
            print("event sequences:", child_terminal[0], root_terminal[0])
            print("SSE replay events:", ", ".join(kinds))
            succeeded = True
    finally:
        if root_id:
            with suppress(Exception):
                await harness_repository.cancel_tree(root_id)
        if thread_id:
            for label, action in (
                ("harness journal", harness_repository.delete_thread(
                    thread_id, owner_name=username
                )),
                ("assistant thread", assistant_repository.delete_thread(
                    thread_id, user_name=username
                )),
                ("run rows", db.execute_system(
                    "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS "
                    "WHERE thread_id = %s AND owner_name = %s", [thread_id, username]
                )),
            ):
                try:
                    await action
                except Exception as exc:
                    cleanup_errors.append(f"{label}: {type(exc).__name__}: {exc}")
        if agent_id:
            for label, action in (
                ("capability", capability_repository.delete(agent_id, username)),
                ("role grant", db.execute_system(
                    "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_ROLES "
                    "WHERE agent_id = %s AND owner_name = %s", [agent_id, username]
                )),
                ("agent", agent_repository.delete_agent(agent_id, owner_name=username)),
            ):
                try:
                    await action
                except Exception as exc:
                    cleanup_errors.append(f"{label}: {type(exc).__name__}: {exc}")
        if session_id:
            try:
                await session_store.delete(session_id)
            except Exception as exc:
                cleanup_errors.append(f"session: {type(exc).__name__}: {exc}")
        if created_user:
            try:
                await db.execute_system(f"DROP USER '{username}'@'%'")
                print("cleanup user: dropped")
            except Exception as exc:
                cleanup_errors.append(f"user: {type(exc).__name__}: {exc}")
        if created_role:
            try:
                await db.execute_system(f"DROP ROLE {role}")
                print("cleanup role: dropped")
            except Exception as exc:
                cleanup_errors.append(f"role: {type(exc).__name__}: {exc}")
        if thread_id:
            for label, table, clause, params in (
                ("runs", "CONFIG_AGENT_RUNS", "thread_id = %s", [thread_id]),
                ("events", "CONFIG_AGENT_SESSION_EVENTS", "root_run_id = %s", [root_id]),
                ("mailbox", "CONFIG_AGENT_MESSAGES", "root_run_id = %s", [root_id]),
                ("thread", "CONFIG_ASSISTANT_THREADS", "thread_id = %s", [thread_id]),
                ("chat", "CONFIG_ASSISTANT_MESSAGES", "thread_id = %s", [thread_id]),
            ):
                try:
                    count = await _count(table, clause, params)
                    print(f"cleanup {label} rows:", count)
                    if count:
                        cleanup_errors.append(f"{label} rows remain: {count}")
                except Exception as exc:
                    cleanup_errors.append(f"{label} count: {type(exc).__name__}: {exc}")
        if agent_id:
            for label, table in (
                ("agent", "CONFIG_AGENTS"),
                ("grant", "CONFIG_AGENT_ROLES"),
                ("capability", "CONFIG_AGENT_CAPABILITIES"),
            ):
                try:
                    count = await _count(table, "agent_id = %s", [agent_id])
                    print(f"cleanup {label} rows:", count)
                    if count:
                        cleanup_errors.append(f"{label} rows remain: {count}")
                except Exception as exc:
                    cleanup_errors.append(f"{label} count: {type(exc).__name__}: {exc}")
        await session_store.close()
        await db.close_system_pool()
        if cleanup_errors:
            raise AssertionError("Cleanup incomplete: " + "; ".join(cleanup_errors))
        if succeeded:
            print("PASS cleanup: zero temporary run/event/message/thread/agent records")


if __name__ == "__main__":
    asyncio.run(main())
