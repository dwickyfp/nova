"""Run the Neva golden set against a live local Nova, as a cautious lay user.

Developer tool for the measured checkpoints in ``docs/testing/neva/``. It signs
in with the local test account, asks each golden question in a fresh thread
with the page context a person would have, and answers approval requests the
way a careful maintainer would: read-only checks are allowed once, anything that
changes Nova is declined (the request itself is recorded, because asking first
is part of the grade). Nothing is changed on the shared stack.

    NOVA_ADMIN_TEST_PASSWORD=... uv run python scripts/neva_golden_run.py \
        --runs 3 --out ../docs/testing/neva/runs/phase-0.jsonl

The password is read from the environment and never printed or written.
Only for local development stacks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.modules.assistant.plain_language import find_technical_leaks  # noqa: E402

SURFACES = {
    "nova.global": ("/", "Nova"),
    "workspace.sql": ("/workspaces", "Workspaces"),
    "intelligence.semantic": ("/semantic-views", "Semantic Views"),
    "roles.list": ("/roles", "Roles"),
    "access.control": ("/access-control", "Access Control"),
    "monitoring.queries": ("/monitoring/queries", "Queries"),
    "monitoring.query_history": ("/query-history", "Query history"),
    "users.list": ("/users", "Users"),
    "database_explorer": ("/database-explorer", "Database Explorer"),
    "tasks.list": ("/tasks", "Tasks"),
    "business_ontology": ("/business-ontology", "Business Ontology"),
}


def app_context(task: dict[str, Any]) -> dict[str, Any]:
    route, title = SURFACES[task["surface"]]
    context: dict[str, Any] = {
        "version": 1,
        "surface": {"id": task["surface"], "route": route, "title": title},
    }
    if "entity" in task:
        context["entity"] = task["entity"]
    if "capabilities" in task:
        # What the page offers Neva, e.g. putting SQL in the editor.
        context["capabilities"] = task["capabilities"]
    if "view" in task:
        context["view"] = task["view"]
    if "editor" in task:
        context["editor"] = {"language": "sql", "selectedText": task["editor"]}
    elif "editor_text" in task:
        # The open file with nothing selected, as the SQL Workspace sends it.
        context["editor"] = {"language": "sql", "text": task["editor_text"]}
    if "execution" in task:
        context["execution"] = {"type": "sql", "executionId": f"run-{task['id']}",
                                **task["execution"]}
        if task["execution"].get("status") == "error" and "editor" in task:
            # What the SQL Workspace sends after a failed run: the failing
            # statement and its error, as a query_failed event.
            context["events"] = [{
                "id": f"event-{task['id']}",
                "timestamp": datetime.now(UTC).isoformat(),
                "source": "execution",
                "type": "query_failed",
                "surfaceId": task["surface"],
                "executionId": f"run-{task['id']}",
                "status": "failure",
                "payload": {
                    "executionId": f"run-{task['id']}",
                    "sql": task["editor"],
                    "errorMessage": task["execution"].get("errorMessage", ""),
                },
            }]
    return context


async def sign_in(client: httpx.AsyncClient, username: str) -> str:
    password = os.environ.get("NOVA_ADMIN_TEST_PASSWORD")
    if not password:
        raise SystemExit("Set NOVA_ADMIN_TEST_PASSWORD (read from docker/.env).")
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


async def run_turn(
    client: httpx.AsyncClient, task: dict[str, Any], timeout: float, keep: bool = False
) -> dict:
    record: dict[str, Any] = {
        "task": task["id"],
        "thread_id": None,
        "text": "",
        "tools": [],
        "consent_requested": [],
        "errors": [],
        "finish_reason": None,
        "events": {},
        "page_actions": [],
        "source": None,
    }
    started = time.monotonic()
    first_token: float | None = None
    thread_id = None
    try:
        thread = (await client.post("/api/v1/assistant/threads", json={})).json()
        thread_id = record["thread_id"] = thread.get("thread_id") or thread.get("id")
    except httpx.HTTPError as exc:
        record["errors"].append(f"thread not created: {type(exc).__name__}")
    body = {"content": task["prompt"], "app_context": app_context(task)}
    try:
        if thread_id is None:
            raise httpx.HTTPError("no thread")
        async with client.stream(
            "POST",
            f"/api/v1/assistant/threads/{thread_id}/messages",
            json=body,
            timeout=httpx.Timeout(timeout, read=timeout),
        ) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode(errors="replace")[:200]
                record["errors"].append(f"status {response.status_code}: {body}")
                raise httpx.HTTPError("refused")
            event = None
            async for line in response.aiter_lines():
                if time.monotonic() - started > timeout:
                    record["errors"].append("timeout")
                    break
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                    continue
                if not line.startswith("data:") or event is None:
                    continue
                data = json.loads(line.split(":", 1)[1].strip() or "{}")
                record["events"][event] = record["events"].get(event, 0) + 1
                if event == "text_delta":
                    if first_token is None:
                        first_token = time.monotonic() - started
                    record["text"] += data.get("text", "")
                elif event == "tool_call":
                    record["tools"].append(
                        {
                            "name": data.get("tool_name"),
                            "classification": data.get("classification"),
                            "status": data.get("status"),
                        }
                    )
                    if data.get("status") == "pending":
                        allow = data.get("classification") == "read_only"
                        if not allow:
                            record["consent_requested"].append(data.get("tool_name"))
                        await client.post(
                            f"/api/v1/assistant/tool-calls/{data['tool_call_id']}/decision",
                            json={"decision": "allow_once" if allow else "deny"},
                        )
                elif event == "client_action":
                    # The page action Neva asked for (e.g. SQL put in the editor).
                    record["page_actions"].append({
                        "capability": data.get("capability"),
                        "args": data.get("args"),
                    })
                elif event == "error":
                    record["errors"].append(data.get("message", ""))
                elif event == "done":
                    record["finish_reason"] = data.get("finish_reason")
                    record["usage"] = data.get("usage")
                    record["source"] = data.get("source")
                    # Read to the end like the panel does, so the reply is stored.
    except httpx.HTTPError as exc:
        if str(exc) != "refused":
            record["errors"].append(type(exc).__name__)
    finally:
        # The transcript is kept in the run file; the test thread is not left
        # in the person's history.
        if thread_id and not keep:
            try:
                await client.delete(f"/api/v1/assistant/threads/{thread_id}")
            except httpx.HTTPError:
                record["errors"].append("thread cleanup failed")
    record["seconds"] = round(time.monotonic() - started, 2)
    record["first_token_seconds"] = round(first_token, 2) if first_token else None
    record["leaks"] = [f"{leak.kind}:{leak.text}" for leak in find_technical_leaks(record["text"])]
    return record


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--user", default="nova_admin")
    parser.add_argument("--golden", default="../docs/testing/neva/golden-set.json")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--out", required=True)
    # Keep the test threads so their stored plans can be read; delete them after.
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    tasks = json.loads(Path(args.golden).read_text())["tasks"]
    if args.only:
        tasks = [task for task in tasks if task["id"] in set(args.only)]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    batch = uuid.uuid4().hex[:8]
    # Resume: turns already in the file are not asked again.
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            row = json.loads(line)
            done.add((row["task"], row["run"]))
    async with httpx.AsyncClient(base_url=args.base, timeout=60) as client:
        token = await sign_in(client, args.user)
        client.headers["Authorization"] = f"Bearer {token}"
        for run in range(args.runs):
            for task in tasks:
                if (task["id"], run) in done:
                    continue
                record = await run_turn(client, task, args.timeout, args.keep)
                record.update(
                    batch=batch, run=run, at=datetime.now(UTC).isoformat(timespec="seconds")
                )
                with out.open("a") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(
                    f"{record['task']} run {run}: {record['seconds']}s "
                    f"finish={record['finish_reason']} tools={len(record['tools'])} "
                    f"consent={record['consent_requested']} leaks={len(record['leaks'])}",
                    flush=True,
                )


if __name__ == "__main__":
    asyncio.run(main())
