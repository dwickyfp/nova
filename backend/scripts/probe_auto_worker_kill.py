"""Exercise real Auto worker loss and recovery against local dummy agents.

Run with the API and infrastructure up, but with no other Auto worker polling::

    STARROCKS_FE_MYSQL_PORT=29030 RANGER_ENABLED=true \
      .venv/bin/python -m scripts.probe_auto_worker_kill

The probe kills a gated root worker, then a gated child worker. It backdates
only the claimed dummy run after SIGKILL so the replacement worker can apply
the normal 90-second stale lease policy immediately.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import sys
import tempfile
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.core.database import db
from app.core.redis import session_store
from app.core.security import create_access_token, encrypt_password
from app.modules.agents.access import access_fingerprint
from app.modules.agents.capabilities import CapabilityManifest, capability_repository
from app.modules.agents.harness_repository import TERMINAL, harness_repository
from app.modules.agents.repository import agent_repository
from app.modules.assistant.repository import assistant_repository
from app.modules.auth.service import auth_service
from scripts.smoke_auto_two_agents import BASE_URL, _chat_model, _request

TIMEOUT_SECONDS = 300


async def _sample_agent(
    owner: str, role: str, provider_id: str, model_name: str
) -> str:
    agent = await agent_repository.create_agent(
        owner_name=owner,
        fields={
            "name": "Worker Recovery Specialist",
            "description": "Synthetic specialist for a worker recovery drill.",
            "database_name": None,
            "schema_name": None,
            "model_provider_id": provider_id,
            "model_name": model_name,
            "instructions_response": (
                "Explain the synthetic recovery label in one sentence. "
                "The exact marker is RECOVERY_DEMO_OK. "
                "It is a demonstration marker, never a measured value."
            ),
            "instructions_orchestration": (
                "You are a specialist. Complete the delegated answer concisely."
            ),
            "response_style": "concise",
            "sample_questions": ["Explain the synthetic recovery label"],
            "budget_seconds": 90,
            "budget_tokens": 12000,
            "tool_not_accessible": "accept",
            "default_tools": [],
            "default_skills": [],
            "policy": "auto_read_only",
            "semantic_model_id": None,
            "visibility": "private",
        },
    )
    await agent_repository.add_agent_role(
        agent["agent_id"], owner_name=owner, role_name=role
    )
    await agent_repository.set_agent_role_verification(
        agent["agent_id"], owner_name=owner, role_name=role,
        fingerprint=await access_fingerprint(agent),
    )
    await capability_repository.put(
        agent,
        CapabilityManifest(
            delegation_description="Explain the synthetic worker recovery label.",
            capability_tags=["recovery"],
            owns=["recovery_demo_label"],
            good_for=["synthetic recovery label"],
            available_to_auto=True,
        ),
    )
    return agent["agent_id"]


async def _start_worker(
    *, kind: str | None = None, root_id: str | None = None,
    marker: Path | None = None, log: object,
) -> asyncio.subprocess.Process:
    worker_env = os.environ.copy()
    worker_env["NOVA_METRICS_AGENT_WORKER_PORT"] = "0"
    command = [sys.executable, "-m", "app.agent_worker"]
    if kind:
        assert root_id and marker
        worker_env.update({
            "NOVA_KILL_PROBE_KIND": kind,
            "NOVA_KILL_PROBE_ROOT_ID": root_id,
            "NOVA_KILL_PROBE_MARKER": str(marker),
        })
        command = [sys.executable, "-m", "scripts.agent_worker_kill_gate"]
    return await asyncio.create_subprocess_exec(
        *command, env=worker_env, stdout=log,
        stderr=asyncio.subprocess.STDOUT,
    )


async def _stop_worker(worker: asyncio.subprocess.Process | None) -> None:
    if worker is None or worker.returncode is not None:
        return
    worker.terminate()
    try:
        await asyncio.wait_for(worker.wait(), timeout=15)
    except TimeoutError:
        worker.kill()
        await worker.wait()


async def _gate_claim(marker: Path, worker: asyncio.subprocess.Process) -> dict:
    deadline = asyncio.get_running_loop().time() + TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        if worker.returncode is not None:
            raise AssertionError(f"Gated worker exited before claim: {worker.returncode}")
        if marker.exists():
            value = json.loads(marker.read_text())
            run = await harness_repository.get(value["run_id"])
            if (
                run and run["status"] == "running"
                and run["lease_owner"] == value["lease_owner"]
                and run["generation"] == value["generation"]
            ):
                return value
        await asyncio.sleep(0.2)
    raise AssertionError("Gated worker never claimed the dummy run")


async def _wait_status(run_id: str, wanted: set[str]) -> dict:
    deadline = asyncio.get_running_loop().time() + TIMEOUT_SECONDS
    last: dict | None = None
    while asyncio.get_running_loop().time() < deadline:
        with suppress(RuntimeError, ValueError):
            last = await harness_repository.get(run_id)
        if last and last["status"] in wanted:
            return last
        await asyncio.sleep(0.25)
    raise AssertionError(f"Run {run_id} never reached {wanted}: {last}")


async def _kill_and_backdate(
    worker: asyncio.subprocess.Process, claim: dict
) -> None:
    worker.kill()
    await asyncio.wait_for(worker.wait(), timeout=10)
    assert worker.returncode == -signal.SIGKILL, worker.returncode
    old = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=180)
    changed = await db.execute_system(
        "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET updated_at = %s "
        "WHERE run_id = %s AND status = 'running' "
        "AND lease_owner = %s AND generation = %s",
        [old, claim["run_id"], claim["lease_owner"], claim["generation"]],
    )
    assert changed.get("affected") == 1, (
        "Only the killed worker's claimed dummy run may be backdated"
    )


async def _turn(
    client: httpx.AsyncClient, provider_id: str, model_name: str,
    title: str,
) -> tuple[str, str]:
    thread = await _request(
        client, "POST", f"{BASE_URL}/__auto__/threads", json={"title": title}
    )
    thread_id = thread["thread_id"]
    question = (
        "This is a synthetic worker recovery test. Delegate the synthetic "
        "recovery label to the accessible specialist and explain what it means. "
        "There is no business data, calculation, or measurement."
    )
    async with client.stream(
        "POST", f"{BASE_URL}/__auto__/threads/{thread_id}/messages",
        json={"content": question, "provider_id": provider_id, "model": model_name},
    ) as response:
        assert response.status_code == 200, response.status_code
        root_id = response.headers.get("X-Nova-Run-ID")
        assert root_id, "Auto turn did not expose its run ID"
    return thread_id, root_id


async def _replay(
    client: httpx.AsyncClient, thread_id: str, root_id: str,
    expected_child: str,
) -> tuple[list[dict], list[dict], dict]:
    deadline = asyncio.get_running_loop().time() + 30
    expected_final = str(uuid5(NAMESPACE_URL, f"nova:auto:final:{root_id}"))
    while True:
        try:
            tree = await _request(client, "GET", f"{BASE_URL}/auto/runs/{root_id}")
            events = await _request(client, "GET", f"{BASE_URL}/auto/runs/{root_id}/events")
            detail = await _request(
                client, "GET", f"{BASE_URL}/__auto__/threads/{thread_id}"
            )
            if (
                any(item["run_id"] == expected_child for item in tree["runs"])
                and any(
                    item["run_id"] == root_id
                    and item["type"] == "agent_completed"
                    for item in events["events"]
                )
                and any(
                    item["message_id"] == expected_final
                    for item in detail["messages"]
                )
            ):
                return tree["runs"], events["events"], detail
        except RuntimeError:
            if asyncio.get_running_loop().time() >= deadline:
                raise
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("Auto replay remained unavailable")
        await asyncio.sleep(0.25)


async def _one_final(detail: dict, root_id: str) -> str:
    expected = str(uuid5(NAMESPACE_URL, f"nova:auto:final:{root_id}"))
    finals = [
        item for item in detail["messages"]
        if item["role"] == "assistant" and item["message_id"] == expected
    ]
    assert len(finals) == 1, "Exactly one deterministic final is required"
    return str(finals[0]["content"])


async def main() -> None:
    suffix = secrets.token_hex(4)
    username = f"nova_kill_probe_{suffix}"
    role = f"kill_probe_{suffix}"
    password = secrets.token_hex(20)
    created_user = False
    created_role = False
    agent_id: str | None = None
    session_id: str | None = None
    thread_ids: list[str] = []
    root_ids: list[str] = []
    worker: asyncio.subprocess.Process | None = None
    worker_log = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115 - closed in finally
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
        agent_id = await _sample_agent(username, role, provider_id, model_name)
        session_id = await session_store.create(
            username, encrypt_password(password), [role],
            default_role=role, active_role=role,
        )
        token = create_access_token(username, session_id)
        async with httpx.AsyncClient(
            timeout=20, headers={"Authorization": f"Bearer {token}"}
        ) as client:
            with tempfile.TemporaryDirectory(prefix="nova-worker-kill-") as directory:
                marker = Path(directory) / "root-claimed.json"
                thread_id, root_id = await _turn(
                    client, provider_id, model_name, "Killed root recovery"
                )
                thread_ids.append(thread_id)
                root_ids.append(root_id)
                print("root kill run:", root_id, flush=True)
                worker = await _start_worker(
                    kind="root", root_id=root_id, marker=marker, log=worker_log
                )
                root_claim = await _gate_claim(marker, worker)
                await _kill_and_backdate(worker, root_claim)
                worker = await _start_worker(log=worker_log)
                root = await _wait_status(root_id, TERMINAL)
                assert root["status"] == "completed", root
                tree = await harness_repository.tree(
                    root_id, owner_name=username, role_name=role
                )
                children = [item for item in tree if item["depth"] == 1]
                assert len(children) == 1 and children[0]["status"] == "completed", tree
                runs, events, detail = await _replay(
                    client, thread_id, root_id, children[0]["run_id"]
                )
                assert next(item for item in runs if item["run_id"] == root_id)[
                    "status"
                ] == "completed"
                kinds = [event["type"] for event in events]
                assert "agent_resumed" in kinds, kinds
                assert sum(
                    event["type"] == "agent_completed" and event["run_id"] == root_id
                    for event in events
                ) == 1, kinds
                assert (await _one_final(detail, root_id)).strip()
                assert root["generation"] >= 2
                print("PASS root SIGKILL recovery", root_id, children[0]["run_id"])
                await _stop_worker(worker)
                worker = None

                marker = Path(directory) / "child-claimed.json"
                thread_id, root_id = await _turn(
                    client, provider_id, model_name, "Killed child safety"
                )
                thread_ids.append(thread_id)
                root_ids.append(root_id)
                print("child kill run:", root_id, flush=True)
                worker = await _start_worker(
                    kind="child", root_id=root_id, marker=marker, log=worker_log
                )
                child_claim = await _gate_claim(marker, worker)
                await _wait_status(root_id, {"waiting_for_agent"})
                await _kill_and_backdate(worker, child_claim)
                worker = await _start_worker(log=worker_log)
                child = await _wait_status(child_claim["run_id"], TERMINAL)
                assert child["status"] == "interrupted", child
                root = await _wait_status(root_id, TERMINAL)
                assert root["status"] == "completed", root
                runs, events, detail = await _replay(
                    client, thread_id, root_id, child_claim["run_id"]
                )
                assert next(
                    item for item in runs if item["run_id"] == child_claim["run_id"]
                )["status"] == "interrupted"
                kinds = [event["type"] for event in events]
                assert any(
                    event["type"] == "agent_interrupted"
                    and event["run_id"] == child_claim["run_id"]
                    for event in events
                ), kinds
                assert not any(
                    event["type"] == "agent_completed"
                    and event["run_id"] == child_claim["run_id"]
                    for event in events
                ), kinds
                assert child["generation"] == 1
                assert await _one_final(detail, root_id)
                print("PASS child SIGKILL interruption", root_id, child_claim["run_id"])
                print("child event types:", ", ".join(kinds))
                await _stop_worker(worker)
                worker = None
    except Exception:
        print("probe run IDs:", root_ids)
        worker_log.seek(0)
        print("worker diagnostic:", worker_log.read().decode(errors="replace")[-4000:])
        raise
    finally:
        await _stop_worker(worker)
        worker_log.close()
        for root_id in root_ids:
            with suppress(Exception):
                await harness_repository.cancel_tree(root_id)
        for thread_id in thread_ids:
            with suppress(Exception):
                await harness_repository.delete_thread(thread_id, owner_name=username)
            with suppress(Exception):
                await assistant_repository.delete_thread(thread_id, user_name=username)
            with suppress(Exception):
                await db.execute_system(
                    "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS "
                    "WHERE thread_id = %s AND owner_name = %s",
                    [thread_id, username],
                )
        if agent_id:
            with suppress(Exception):
                await capability_repository.delete(agent_id, username)
            with suppress(Exception):
                await db.execute_system(
                    "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_ROLES "
                    "WHERE agent_id = %s AND owner_name = %s", [agent_id, username]
                )
            with suppress(Exception):
                await agent_repository.delete_agent(agent_id, owner_name=username)
        if session_id:
            await session_store.delete(session_id)
        if created_user:
            await db.execute_system(f"DROP USER '{username}'@'%'")
        if created_role:
            await db.execute_system(f"DROP ROLE {role}")
        await session_store.close()
        await db.close_system_pool()


if __name__ == "__main__":
    asyncio.run(main())
