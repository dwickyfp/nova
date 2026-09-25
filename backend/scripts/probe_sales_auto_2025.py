"""Compare the configured Sales Agent and Auto on the real 2025 dummy query.

Requires the local API, agent worker, Redis, StarRocks, and an existing
``nova_admin`` session with ACCOUNTADMIN active. Reuses that session without
reading or printing its password. Leaves the two test threads and runs in
place so their evidence can be inspected in Nova Studio.

Run from ``backend/`` with the same environment as ``dev.sh``::

    STARROCKS_FE_MYSQL_PORT=29030 RANGER_ENABLED=true \
      .venv/bin/python -m scripts.probe_sales_auto_2025
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx

from app.core.database import db
from app.core.redis import SESSION_PREFIX, session_store
from app.core.security import create_access_token
from app.modules.agents.harness_repository import TERMINAL, harness_repository
from app.modules.assistant.answer_contract import check_numeric_answer

BASE_URL = "http://127.0.0.1:8000/api/v1/agents"
QUESTION = "Bandingkan recognized revenue, gross margin, dan order count per channel untuk 2025."
BASELINE_SQL = (
    "SELECT sales_channel, SUM(net_revenue) AS recognized_revenue, "
    "SUM(gross_profit) / NULLIF(SUM(net_revenue), 0) AS gross_margin_pct, "
    "COUNT(DISTINCT sale_id) AS order_count FROM NOVA_SALES.fact_sales "
    "WHERE order_status <> 'Cancelled' AND order_date >= '2025-01-01' "
    "AND order_date < '2026-01-01' GROUP BY sales_channel "
    "ORDER BY recognized_revenue DESC"
)
REQUIRED_COLUMNS = (
    "sales_channel", "recognized_revenue", "gross_margin_pct", "order_count"
)


async def _admin_session() -> str:
    redis = session_store._redis
    assert redis is not None
    candidates: list[tuple[int, str]] = []
    async for key in redis.scan_iter(match=f"{SESSION_PREFIX}*"):
        username, role, ttl = await asyncio.gather(
            redis.hget(key, "username"),
            redis.hget(key, "active_role"),
            redis.ttl(key),
        )
        if username == "nova_admin" and role == "ACCOUNTADMIN" and ttl > 0:
            candidates.append((ttl, key.removeprefix(SESSION_PREFIX)))
    if not candidates:
        raise RuntimeError("No active nova_admin ACCOUNTADMIN session")
    return max(candidates)[1]


async def _sales_agent() -> tuple[str, str, str]:
    result = await db.execute_system(
        "SELECT agent_id, model_provider_id, model_name "
        "FROM NOVA_SYSTEM.CONFIG_AGENTS "
        "WHERE owner_name = 'nova_admin' AND name = 'Sales Agent'"
    )
    if len(result["rows"]) != 1:
        raise AssertionError(f"Expected one configured Sales Agent, got {len(result['rows'])}")
    agent_id, provider_id, model_name = result["rows"][0]
    if not all((agent_id, provider_id, model_name)):
        raise AssertionError("Sales Agent has no complete chat-model configuration")
    return str(agent_id), str(provider_id), str(model_name)


def _matching_table(tables: list[dict[str, Any]]) -> dict[str, Any]:
    for table in tables:
        columns = [str(column).casefold() for column in table.get("columns") or []]
        if all(name in columns for name in REQUIRED_COLUMNS):
            return table
    raise AssertionError("No query result contains all four requested metrics")


def _rows(table: dict[str, Any]) -> dict[str, tuple[Decimal, Decimal, Decimal]]:
    columns = [str(column).casefold() for column in table["columns"]]
    positions = [columns.index(name) for name in REQUIRED_COLUMNS]
    rows: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for raw in table["rows"]:
        channel, revenue, margin, count = (raw[position] for position in positions)
        label = str(channel)
        if label in rows:
            raise AssertionError(f"Duplicate channel in result: {label}")
        rows[label] = (Decimal(str(revenue)), Decimal(str(margin)), Decimal(str(count)))
    return rows


def _assert_matches_baseline(table: dict[str, Any], baseline: dict[str, Any]) -> None:
    actual = _rows(table)
    expected = _rows(baseline)
    if actual.keys() != expected.keys():
        raise AssertionError(f"Channel set mismatch: {sorted(actual)} vs {sorted(expected)}")
    for channel in expected:
        observed, reference = actual[channel], expected[channel]
        if observed[0] != reference[0] or observed[2] != reference[2]:
            raise AssertionError(f"Revenue or count differs for {channel}")
        if abs(observed[1] - reference[1]) > Decimal("0.00000001"):
            raise AssertionError(f"Gross margin differs for {channel}")


def _assert_answer(
    answer: str,
    baseline: dict[str, Any],
    label: str,
    *,
    evidence: list[dict[str, Any]] | None = None,
) -> None:
    if not answer.strip():
        raise AssertionError(f"{label} saved no final answer")
    lowered = answer.casefold()
    if "could not verify every number" in lowered or "tidak dapat memverifikasi" in lowered:
        raise AssertionError(f"{label} returned a numeric-verification fallback")
    for channel in _rows(baseline):
        if channel.casefold() not in lowered:
            raise AssertionError(f"{label} omitted {channel}")
    metrics = _rows(baseline)
    for name, position in (
        ("recognized revenue", 0),
        ("gross margin pct", 1),
        ("order count", 2),
    ):
        highest = max(metrics, key=lambda channel: metrics[channel][position])
        lowest = min(metrics, key=lambda channel: metrics[channel][position])
        claim = f"{name}: tertinggi {highest}; terendah {lowest}."
        if claim.casefold() not in lowered:
            raise AssertionError(f"{label} misstated {name} ranking")
    tables = (
        {f"authorized_{index}": table for index, table in enumerate(evidence)}
        if evidence is not None else {"baseline": baseline}
    )
    checked = check_numeric_answer(answer, question=QUESTION, tables=tables)
    if not checked.accepted:
        raise AssertionError(f"{label} contains unsupported numeric claims: {checked.unsupported}")


async def _request(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> dict:
    response = await client.request(method, f"{BASE_URL}/{path.lstrip('/')}", **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path} returned HTTP {response.status_code}")
    return response.json()


async def _sse(response: httpx.Response) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    kind = ""
    data: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("event: "):
            kind = line[7:]
        elif line.startswith("data: "):
            data.append(line[6:])
        elif not line and kind:
            yield kind, json.loads("\n".join(data)) if data else {}
            kind, data = "", []


async def _stream_run(
    client: httpx.AsyncClient, path: str, *, body: dict[str, Any] | None = None
) -> tuple[str | None, list[tuple[str, dict[str, Any]]]]:
    method = "POST" if body is not None else "GET"
    async with client.stream(method, f"{BASE_URL}/{path}", json=body) as response:
        if response.status_code != 200:
            raise RuntimeError(f"{method} {path} returned HTTP {response.status_code}")
        run_id = response.headers.get("X-Nova-Run-ID")
        frames = [frame async for frame in _sse(response) if frame[0] != "ping"]
    return run_id, frames


async def _auto_events(client: httpx.AsyncClient, root_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    after = ""
    while True:
        page = (await _request(
            client, "GET", f"auto/runs/{root_id}/events", params={"after": after}
        ))["events"]
        if not page:
            break
        events.extend(page)
        if len(page) < 100:
            break
        after = str(page[-1]["event_id"])
    sequences = [int(item["event_id"]) for item in events]
    if sequences != sorted(set(sequences)):
        raise AssertionError("Auto event replay has duplicate or out-of-order sequences")
    return events


async def _child_timeline(
    client: httpx.AsyncClient, root_id: str, child_id: str
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cursor = -1
    while True:
        page = await _request(
            client,
            "GET",
            f"auto/runs/{root_id}/children/{child_id}/timeline",
            params={"after": cursor, "limit": 100},
        )
        run = page["run"]
        if run["run_id"] != child_id or run["root_run_id"] != root_id:
            raise AssertionError("Child timeline returned another run")
        batch = page["events"]
        events.extend(batch)
        next_cursor = int(page["next_cursor"])
        if next_cursor < cursor or (page["has_more"] and next_cursor == cursor):
            raise AssertionError("Child timeline cursor did not advance")
        cursor = next_cursor
        if not page["has_more"]:
            break
    sequences = [int(item["event_id"]) for item in events]
    if sequences != sorted(set(sequences)):
        raise AssertionError("Child timeline has duplicate or out-of-order events")
    return events


async def _saved_final(client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    """Allow bounded visibility lag between run and message Primary Key tables."""
    for attempt in range(25):
        detail = await _request(client, "GET", path)
        finals = [item for item in detail["messages"] if item["role"] == "assistant"]
        if len(finals) == 1:
            return finals[0]
        if len(finals) > 1:
            raise AssertionError(f"Thread has {len(finals)} final messages")
        if attempt < 24:
            await asyncio.sleep(0.2)
    raise AssertionError("Completed run's final message was not visible within five seconds")


async def main() -> None:
    await db.init_system_pool()
    await session_store.init()
    try:
        session_id = await _admin_session()
        agent_id, provider_id, model_name = await _sales_agent()
        baseline = await db.execute_system(BASELINE_SQL)
        baseline = {"columns": baseline["columns"], "rows": baseline["rows"]}
        assert len(_rows(baseline)) == 5, "Expected five 2025 sales channels"
        token = create_access_token("nova_admin", session_id)
        headers = {"Authorization": f"Bearer {token}"}
        timeout = httpx.Timeout(20.0, read=300.0)
        async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
            found = await _request(client, "GET", "capabilities", params={"query": QUESTION})
            if agent_id not in {item["agent_id"] for item in found["agents"]}:
                raise AssertionError("Sales Agent is absent from Auto discovery")

            direct_thread = (await _request(
                client, "POST", f"{agent_id}/threads", json={"title": "Probe: Sales 2025 direct"}
            ))["thread_id"]
            print(f"Direct thread: {direct_thread}")
            direct_run, frames = await _stream_run(
                client, f"{agent_id}/threads/{direct_thread}/messages",
                body={"content": QUESTION, "provider_id": provider_id, "model": model_name},
            )
            if not direct_run:
                raise AssertionError("Direct stream did not expose a run ID")
            print(f"Direct run: {direct_run}")
            done = [payload for kind, payload in frames if kind == "done"]
            if len(done) != 1 or done[0].get("finish_reason") != "stop":
                raise AssertionError(f"Direct run did not finish cleanly: {done}")
            direct_tables = [payload for kind, payload in frames if kind == "table"]
            _assert_matches_baseline(_matching_table(direct_tables), baseline)
            direct_final = await _saved_final(client, f"{agent_id}/threads/{direct_thread}")
            _assert_answer(str(direct_final["content"]), baseline, "Direct Agent")
            _, direct_replay = await _stream_run(
                client, f"{agent_id}/threads/{direct_thread}/runs/{direct_run}/events"
            )
            _assert_matches_baseline(
                _matching_table([payload for kind, payload in direct_replay if kind == "table"]),
                baseline,
            )
            direct_done = [payload for kind, payload in direct_replay if kind == "done"]
            if (
                len(direct_done) != 1
                or direct_done[0].get("message_id") != direct_final["message_id"]
            ):
                raise AssertionError("Direct replay and saved final message disagree")

            auto_thread = (await _request(
                client, "POST", "__auto__/threads", json={"title": "Probe: Sales 2025 Auto"}
            ))["thread_id"]
            print(f"Auto thread: {auto_thread}")
            # Auto starts durably when headers arrive. The worker continues after disconnect.
            async with client.stream(
                "POST", f"{BASE_URL}/__auto__/threads/{auto_thread}/messages",
                json={"content": QUESTION, "provider_id": provider_id, "model": model_name},
            ) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"Auto turn returned HTTP {response.status_code}")
                root_id = response.headers.get("X-Nova-Run-ID")
                if not root_id:
                    raise AssertionError("Auto did not expose a durable run ID")
            print(f"Auto root run: {root_id}")
            deadline = asyncio.get_running_loop().time() + 600
            while asyncio.get_running_loop().time() < deadline:
                try:
                    tree = (await _request(client, "GET", f"auto/runs/{root_id}"))["runs"]
                except RuntimeError as exc:
                    if "HTTP 404" not in str(exc):
                        raise
                    await asyncio.sleep(0.5)
                    continue
                root = next(item for item in tree if item["depth"] == 0)
                if root["status"] in TERMINAL:
                    break
                await asyncio.sleep(1)
            else:
                raise AssertionError("Auto root did not settle within 600 seconds")
            children = [item for item in tree if item["depth"] == 1]
            if root["status"] != "completed":
                raise AssertionError(f"Auto root ended {root['status']}: {root['error_class']}")
            sales_children = [item for item in children if item["agent_id"] == agent_id]
            if len(sales_children) != 1 or sales_children[0]["status"] != "completed":
                raise AssertionError(f"Sales specialist did not complete: {children}")
            if any(item["status"] != "completed" for item in children):
                raise AssertionError("Another Auto specialist failed")
            child = await harness_repository.get(sales_children[0]["run_id"])
            if child is None:
                raise AssertionError("Sales specialist run was not durable")
            evidence = (child.get("checkpoint") or {}).get("evidence_tables") or []
            _assert_matches_baseline(_matching_table(evidence), baseline)
            for attempt in range(25):
                events = await _auto_events(client, root_id)
                completed = {
                    item["run_id"] for item in events if item["type"] == "agent_completed"
                }
                if {root_id, child["run_id"]} <= completed:
                    break
                if attempt < 24:
                    await asyncio.sleep(0.2)
            else:
                raise AssertionError("Auto replay omitted a terminal child or root event")
            replay_id, replay = await _stream_run(
                client, f"__auto__/threads/{auto_thread}/runs/{root_id}/events"
            )
            assert replay_id is None
            replay_done = [payload for kind, payload in replay if kind == "done"]
            if len(replay_done) != 1 or replay_done[0].get("finish_reason") != "stop":
                raise AssertionError("Auto SSE replay did not end with one successful done event")
            auto_final = await _saved_final(client, f"__auto__/threads/{auto_thread}")
            answer = str(auto_final["content"])
            _assert_answer(answer, baseline, "Auto", evidence=evidence)
            if replay_done[0]["message_id"] != auto_final["message_id"]:
                raise AssertionError("Auto replay and saved final message disagree")
            timeline = await _child_timeline(client, root_id, child["run_id"])
            timeline_types = {item["type"] for item in timeline}
            required_child_types = {
                "agent_queued", "agent_started", "child_activity", "agent_completed"
            }
            if not required_child_types <= timeline_types:
                raise AssertionError(f"Child timeline omitted harness events: {timeline_types}")
            answers = [
                item["payload"].get("text")
                for item in timeline
                if item["type"] == "child_activity"
                and item["payload"].get("event_type") == "answer"
            ]
            if len(answers) != 1 or not str(answers[0]).strip():
                raise AssertionError("Child timeline omitted its verified answer")
            runs = (await _request(client, "GET", f"auto/threads/{auto_thread}/runs"))["runs"]
            mapped = next((item for item in runs if item["run_id"] == root_id), None)
            if not mapped or mapped["final_message_id"] != auto_final["message_id"]:
                raise AssertionError("Auto turn-to-run mapping did not survive replay")
            if not mapped.get("user_message_id"):
                raise AssertionError("Auto turn has no originating user message ID")
            print(
                "PASS: five SQL channels, direct result, Auto child evidence, "
                "answers, replay, and child conversation timeline"
            )
            print("Channels:", ", ".join(_rows(baseline)))
            print("Auto child run:", child["run_id"])
            print("Auto events:", len(events))
            print("Child timeline events:", len(timeline))
    finally:
        await session_store.close()
        await db.close_system_pool()


if __name__ == "__main__":
    asyncio.run(main())
