"""Exercise two real Auto specialists through the local API and worker.

Requires the development StarRocks, Redis, API, and a configured chat model.
The script creates a temporary StarRocks user, two agents, a Studio thread, and
a worker process. It injects one synthetic child finding through the durable
mailbox and removes its test records in a finally block.

Run from backend with the same environment as dev.sh::

    STARROCKS_FE_MYSQL_PORT=29030 RANGER_ENABLED=true \
      .venv/bin/python -m scripts.smoke_auto_two_agents

Set NOVA_SMOKE_EXTERNAL_WORKER=1 when dev.sh already runs the agent worker.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
import tempfile
from contextlib import suppress
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

BASE_URL = "http://127.0.0.1:8000/api/v1/agents"
TIMEOUT_SECONDS = 300


async def _chat_model() -> tuple[str, str]:
    result = await db.execute_system(
        "SELECT p.id, m.name FROM NOVA_SYSTEM.CONFIG_AI_PROVIDERS p "
        "JOIN NOVA_SYSTEM.CONFIG_AI_MODELS m ON m.provider_id = p.id "
        "WHERE p.is_active = 1 AND m.is_active = 1 AND m.type = 'llm' "
        "AND LENGTH(p.api_key) > 0 ORDER BY p.name, m.name LIMIT 1"
    )
    if not result["rows"]:
        raise RuntimeError("No active chat model is configured")
    return str(result["rows"][0][0]), str(result["rows"][0][1])


async def _sample_agent(
    owner: str, role: str, provider_id: str, model_name: str,
    *, name: str, domain: str, metric: str, alias: str, marker: str,
) -> str:
    agent = await agent_repository.create_agent(
        owner_name=owner,
        fields={
            "name": name,
            "description": f"Synthetic {domain} specialist for the Auto smoke test.",
            "database_name": None,
            "schema_name": None,
            "model_provider_id": provider_id,
            "model_name": model_name,
            "instructions_response": (
                f"You explain the label {metric}. This is a synthetic test with no business data. "
                f"Include the exact marker {marker} in your final answer. "
                "State that it is a demonstration marker, never a measured value."
            ),
            "instructions_orchestration": (
                "Send a short intermediate finding to Auto with send_agent_message, "
                "then finish your delegated answer."
            ),
            "response_style": "concise",
            "sample_questions": [f"Explain {alias}"],
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
            delegation_description=f"Analyze the synthetic {domain} demo metric.",
            capability_tags=[domain],
            owns=[metric],
            good_for=[alias],
            available_to_auto=True,
        ),
    )
    return agent["agent_id"]


async def _request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> dict:
    response = await client.request(method, url, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(
            f"{method} {url} returned HTTP {response.status_code}: {response.text[:300]}"
        )
    return response.json()


async def _child_timeline(
    client: httpx.AsyncClient, root_run_id: str, child_run_id: str,
) -> dict:
    url = f"{BASE_URL}/auto/runs/{root_run_id}/children/{child_run_id}/timeline"
    first = await _request(client, "GET", url, params={"after": -1, "limit": 3})
    replay = await _request(client, "GET", url, params={"after": -1, "limit": 3})
    assert replay["events"] == first["events"], (
        f"Child timeline first page did not replay events: {child_run_id}: "
        f"{[item['event_id'] for item in first['events']]} != "
        f"{[item['event_id'] for item in replay['events']]}"
    )
    assert replay["next_cursor"] == first["next_cursor"], (
        f"Child timeline first page cursor changed: {child_run_id}"
    )

    items = list(first["events"])
    page = first
    cursor = int(first["next_cursor"])
    for _ in range(100):
        if not page["has_more"]:
            break
        page = await _request(client, "GET", url, params={"after": cursor, "limit": 3})
        next_cursor = int(page["next_cursor"])
        assert next_cursor > cursor, f"Child timeline cursor stalled: {child_run_id}"
        assert page["run"]["run_id"] == child_run_id, (
            f"Child timeline returned another run: {child_run_id}"
        )
        items.extend(page["events"])
        cursor = next_cursor
    else:
        raise AssertionError(f"Child timeline pagination did not finish: {child_run_id}")

    sequences = [int(item["event_id"]) for item in items]
    assert sequences == sorted(set(sequences)), (
        f"Child timeline is duplicated or out of order: {child_run_id}: {sequences}"
    )
    assert all(
        item["run_id"] == child_run_id
        or (
            item["type"] == "agent_message"
            and item["payload"].get("recipient_run_id") == child_run_id
        )
        for item in items
    ), f"Child timeline leaked another run: {child_run_id}"
    return {"run": first["run"], "events": items}


async def main() -> None:
    suffix = secrets.token_hex(4)
    username = f"nova_auto_smoke_{suffix}"
    role = f"auto_smoke_{suffix}"
    password = secrets.token_hex(20)
    agents: list[str] = []
    thread_id: str | None = None
    root_id: str | None = None
    session_id: str | None = None
    worker: asyncio.subprocess.Process | None = None
    smoke_succeeded = False
    worker_log = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115 - closed in finally
    created_role = False
    created_user = False
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

        for spec in (
            dict(name="Auto Smoke Finance", domain="finance", metric="finance_demo_label",
                 alias="finance demo label", marker="FINANCE_DEMO_OK"),
            dict(name="Auto Smoke Marketing", domain="marketing", metric="marketing_demo_label",
                 alias="marketing demo label", marker="MARKETING_DEMO_OK"),
        ):
            agent_id = await _sample_agent(
                username, role, provider_id, model_name, **spec
            )
            agents.append(agent_id)

        session_id = await session_store.create(
            username, encrypt_password(password), [role],
            default_role=role, active_role=role,
        )
        token = create_access_token(username, session_id)
        headers = {"Authorization": f"Bearer {token}"}
        if os.environ.get("NOVA_SMOKE_EXTERNAL_WORKER") != "1":
            worker_env = os.environ.copy()
            worker_env["NOVA_METRICS_AGENT_WORKER_PORT"] = "0"
            worker = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "app.agent_worker",
                env=worker_env,
                stdout=worker_log,
                stderr=asyncio.subprocess.STDOUT,
            )
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            discovered = await _request(
                client, "GET", f"{BASE_URL}/capabilities",
                params={"query": "finance demo label and marketing demo label"},
            )
            visible = {item["agent_id"] for item in discovered["agents"]}
            assert set(agents) <= visible, "Both agents must be discoverable under the test role"
            thread = await _request(
                client, "POST", f"{BASE_URL}/__auto__/threads",
                json={"title": "Two-agent live smoke test"},
            )
            thread_id = thread["thread_id"]
            question = (
                "This is a synthetic test. Delegate to BOTH accessible specialists: "
                "one to explain FINANCE_DEMO_OK and one to explain MARKETING_DEMO_OK. "
                "Describe what each demonstration marker indicates. "
                "No business data, calculations, or measurements are involved."
            )
            async with client.stream(
                "POST", f"{BASE_URL}/__auto__/threads/{thread_id}/messages",
                json={"content": question, "provider_id": provider_id, "model": model_name},
            ) as response:
                assert response.status_code == 200, f"Auto turn returned {response.status_code}"
                root_id = response.headers.get("X-Nova-Run-ID")
                assert root_id, "Auto turn did not expose a durable run ID"

            deadline = asyncio.get_running_loop().time() + TIMEOUT_SECONDS
            statuses: list[str] = []
            sent_messages: set[str] = set()
            sent_findings: set[str] = set()
            transient_scope_errors = 0
            while asyncio.get_running_loop().time() < deadline:
                try:
                    tree = await _request(client, "GET", f"{BASE_URL}/auto/runs/{root_id}")
                except RuntimeError as exc:
                    if "HTTP 404" not in str(exc) or transient_scope_errors >= 5:
                        raise
                    transient_scope_errors += 1
                    await asyncio.sleep(0.25)
                    continue
                runs = tree["runs"]
                children = [run for run in runs if run["depth"] == 1]
                root = next(run for run in runs if run["depth"] == 0)
                statuses = [f"{run['agent_id']}:{run['status']}" for run in runs]
                for active in children:
                    child_id = active["run_id"]
                    if active["status"] not in TERMINAL and child_id not in sent_findings:
                        sender = await harness_repository.get(child_id)
                        recipient = await harness_repository.get(root_id)
                        assert sender and recipient
                        await harness_repository.send(
                            sender=sender,
                            recipient=recipient,
                            operation_id=f"live-smoke-finding:{child_id}",
                            message_type="finding",
                            content=(
                                "Synthetic child finding: demo labels contain no measured values."
                            ),
                        )
                        sent_findings.add(child_id)
                    if active["status"] not in TERMINAL and child_id not in sent_messages:
                        try:
                            result = await _request(
                                client, "POST",
                                f"{BASE_URL}/auto/runs/{root_id}/children/{child_id}/messages",
                                json={
                                    "operation_id": f"live-smoke-context:{child_id}",
                                    "content": (
                                        "User context: these are demonstration markers, "
                                        "not measured values."
                                    ),
                                },
                            )
                        except RuntimeError as exc:
                            if "HTTP 404" not in str(exc):
                                raise
                        else:
                            assert result["message_id"]
                            sent_messages.add(child_id)
                if any(child["status"] == "waiting_for_auth" for child in children):
                    raise AssertionError(f"Specialist access changed during live run: {statuses}")
                if root["status"] in TERMINAL:
                    break
                await asyncio.sleep(0.25)
            else:
                raise AssertionError(f"Auto run did not settle: {statuses}")

            assert root["status"] == "completed", f"Auto failed: {statuses}"
            assert len(children) == 2, f"Expected two specialists: {statuses}"
            assert all(run["status"] == "completed" for run in children), statuses
            kinds: list[str] = []
            for _ in range(40):
                events = await _request(client, "GET", f"{BASE_URL}/auto/runs/{root_id}/events")
                kinds = [event["type"] for event in events["events"]]
                if "agent_waiting" in kinds and kinds.count("agent_completed") >= 3:
                    break
                await asyncio.sleep(0.25)
            assert "agent_waiting" in kinds and kinds.count("agent_completed") >= 3, kinds
            child_ids = {child["run_id"] for child in children}
            assert sent_messages == child_ids, "User-to-child messages missed a specialist"
            assert sent_findings == child_ids, "Child-to-root findings missed a specialist"
            messages = await _request(client, "GET", f"{BASE_URL}/auto/runs/{root_id}/messages")
            for child_id in child_ids:
                assert any(
                    item["origin"] == "user"
                    and item["sender_run_id"] == root_id
                    and item["recipient_run_id"] == child_id
                    for item in messages["messages"]
                ), f"User-to-child coordination message was not durable: {child_id}"
                assert any(
                    item["origin"] == "agent"
                    and item["sender_run_id"] == child_id
                    and item["recipient_run_id"] == root_id
                    and item["consumed_at"] is not None
                    for item in messages["messages"]
                ), f"Child-to-root finding was not consumed: {child_id}"

            timeline_counts: list[str] = []
            for child in children:
                child_id = child["run_id"]
                timeline = await _child_timeline(client, root_id, child_id)
                assert timeline["run"]["status"] == "completed", child_id
                assert timeline["run"]["agent_id"] == child["agent_id"], child_id
                items = timeline["events"]
                event_types = [item["type"] for item in items]
                assert "agent_queued" in event_types, f"Child queue status missing: {child_id}"
                assert "agent_started" in event_types, f"Child start status missing: {child_id}"
                assert "agent_completed" in event_types, f"Child completion missing: {child_id}"
                assert (
                    event_types.index("agent_queued") < event_types.index("agent_started")
                ), child_id
                assert (
                    event_types.index("agent_started") < event_types.index("agent_completed")
                ), child_id
                activities = [
                    item["payload"].get("event_type") for item in items
                    if item["type"] == "child_activity"
                ]
                assert any(kind != "answer" for kind in activities), (
                    f"Child harness activity missing: {child_id}"
                )
                assert "answer" in activities, f"Child answer missing: {child_id}"
                assert (
                    event_types.index("child_activity") < event_types.index("agent_completed")
                ), child_id
                for sender_id, recipient_id, origin in (
                    (root_id, child_id, "user"),
                    (child_id, root_id, "agent"),
                ):
                    assert any(
                        item["type"] == "agent_message"
                        and item["payload"].get("sender_run_id") == sender_id
                        and item["payload"].get("recipient_run_id") == recipient_id
                        and item["payload"].get("origin") == origin
                        for item in items
                    ), f"Child timeline lacks {origin} coordination: {child_id}"
                timeline_counts.append(f"{child_id}:{len(items)}")
            expected_final_id = str(uuid5(NAMESPACE_URL, f"nova:auto:final:{root_id}"))
            final: list[dict] = []
            for _ in range(40):
                detail = await _request(
                    client, "GET", f"{BASE_URL}/__auto__/threads/{thread_id}"
                )
                final = [
                    item for item in detail["messages"]
                    if item["role"] == "assistant"
                ]
                if len(final) == 1 and final[0]["message_id"] == expected_final_id:
                    break
                await asyncio.sleep(0.25)
            assert len(final) == 1, "Auto must write one final Studio message"
            assert final[0]["message_id"] == expected_final_id, (
                "Auto final Studio message must have the deterministic run ID"
            )
            final_text = str(final[0]["content"])
            assert all(
                marker in final_text for marker in ("FINANCE_DEMO_OK", "MARKETING_DEMO_OK")
            ), "Auto final answer must include both specialist findings"
            print(
                "PASS two specialists completed; both timelines replayed ordered "
                "status, activity, answer, and bidirectional messages; one final persisted"
            )
            print("root run:", root_id)
            print("run statuses:", ", ".join(statuses))
            print("event types:", ", ".join(kinds))
            print("child timeline events:", ", ".join(timeline_counts))
            print("user-to-child messages:", len(sent_messages))
            print("child-to-root findings:", len(sent_findings))
            smoke_succeeded = True
    except Exception:
        if root_id:
            result = await db.execute_system(
                "SELECT run_id, owner_name, agent_id, role_name, status, depth, thread_id "
                "FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS WHERE run_id = %s",
                [root_id],
            )
            print("root row on failure:", result["rows"])
            related = await db.execute_system(
                "SELECT run_id, root_run_id, parent_run_id, agent_id, "
                "status, depth, error_class, result_summary "
                "FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS WHERE root_run_id = %s",
                [root_id],
            )
            print("related rows on failure:", related["rows"])
            messages = await db.execute_system(
                "SELECT recipient_run_id, message_type, correlation_id, "
                "reply_to, content, origin FROM NOVA_SYSTEM.CONFIG_AGENT_MESSAGES "
                "WHERE root_run_id = %s", [root_id]
            )
            print("coordination rows on failure:", messages["rows"])
            events = await db.execute_system(
                "SELECT session_sequence, run_id, event_type FROM "
                "NOVA_SYSTEM.CONFIG_AGENT_SESSION_EVENTS WHERE root_run_id = %s "
                "ORDER BY session_sequence", [root_id]
            )
            print("event rows on failure:", events["rows"])
            decoded = await harness_repository.get(root_id)
            print(
                "decoded root on failure:",
                {key: decoded.get(key) for key in (
                    "run_id", "owner_name", "agent_id", "role_name", "depth",
                    "thread_id", "status", "security_version", "error_class",
                )} if decoded else None,
            )
        if session_id:
            session = await session_store.get(session_id)
            print(
                "session scope on failure:",
                {key: session.get(key) for key in (
                    "username", "active_role", "security_context_version"
                )}
                if session else None,
            )
            if root_id:
                for child in await harness_repository.tree(
                    root_id, owner_name=username, role_name=role
                ):
                    if child["depth"] != 1:
                        continue
                    agent = await agent_repository.get_agent(
                        child["agent_id"], owner_name=username
                    )
                    grants = await agent_repository.list_agent_roles(
                        child["agent_id"], owner_name=username
                    )
                    verified = next(
                        (item.get("verified_fingerprint") for item in grants
                         if item["role_name"] == role), None
                    )
                    current = await access_fingerprint(agent) if agent else None
                    print("child access on failure:", {
                        "run_id": child["run_id"],
                        "status": child["status"],
                        "security_version": child["security_version"],
                        "agent_visible": agent is not None,
                        "role_verified": verified is not None,
                        "fingerprint_matches": verified == current,
                    })
        if worker:
            print("worker exit code on failure:", worker.returncode)
            worker_log.seek(0)
            print("worker log on failure:", worker_log.read().decode(errors="replace")[-2000:])
        raise
    finally:
        unexpected_worker_exit = False
        worker_diagnostic = ""
        if worker:
            if worker.returncode is None:
                try:
                    worker.terminate()
                except ProcessLookupError:
                    unexpected_worker_exit = True
            else:
                unexpected_worker_exit = True
            try:
                await asyncio.wait_for(worker.wait(), timeout=10)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    worker.kill()
                await worker.wait()
            if unexpected_worker_exit:
                worker_log.seek(0)
                worker_diagnostic = worker_log.read().decode(errors="replace")[-2000:]
        worker_log.close()
        if root_id:
            with suppress(Exception):
                await harness_repository.cancel_tree(root_id)
        if thread_id:
            with suppress(Exception):
                await harness_repository.delete_thread(thread_id, owner_name=username)
            with suppress(Exception):
                await assistant_repository.delete_thread(thread_id, user_name=username)
            with suppress(Exception):
                await db.execute_system(
                    "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_RUNS "
                    "WHERE thread_id = %s AND owner_name = %s", [thread_id, username]
                )
        for agent_id in agents:
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
        if smoke_succeeded and unexpected_worker_exit:
            raise AssertionError(
                f"Smoke worker exited unexpectedly ({worker.returncode}): "
                f"{worker_diagnostic}"
            )


if __name__ == "__main__":
    asyncio.run(main())
