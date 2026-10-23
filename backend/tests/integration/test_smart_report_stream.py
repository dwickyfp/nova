"""Real report HTTP/SSE and FE authorization with a deterministic provider seam."""

import asyncio
import json
import os
import socket
from pathlib import Path

import pytest
import uvicorn

from app.core.database import db
from app.modules.assistant.provider import AssistantProviderClient
from app.modules.smart_reports.repository import report_repository
from tests.benchmark.harness import text_frame, tool_call_frame
from tests.integration.test_governed_studio_ranger import governed as governed

pytestmark = [
    pytest.mark.engine,
    pytest.mark.skipif(
        os.getenv("NOVA_GOVERNED_STUDIO_RANGER_ACCEPTANCE") != "1",
        reason="Requires existing patched FE, Ranger, frontend dependencies and Chromium",
    ),
]


async def test_prompt_consent_stream_replay_and_handoff(governed, monkeypatch, tmp_path):
    fixture = governed
    await report_repository.ensure_schema()
    agent_id = fixture.agent["agent_id"]
    thread_id = fixture.thread["thread_id"]
    await fixture.primary.request("PUT", f"agents/{agent_id}", {
        "default_tools": ["smart_report"], "harness_mode": "fast",
    })
    verification = await fixture.primary.request(
        "POST", f"agents/{agent_id}/access/verify", {"role_name": fixture.role},
    )
    assert verification["all_granted"]
    spec = {
        "title": "Prompt revenue",
        "sources": [{"id": "sales", **fixture.semantic}],
        "filters": [{"id": "city", "label": "City", "source": "sales", "field": "city",
                     "kind": "single", "default": {"kind": "single", "values": ["Jakarta"]}}],
        "pages": [{"id": "overview", "title": "Overview", "sections": [{
            "id": "main", "widgets": [{
                "id": "revenue", "title": "Revenue", "kind": "kpi",
                "query": {"source": "sales", "metrics": ["revenue"]},
            }],
        }]}],
    }
    calls = []

    async def complete(_self, **kwargs):
        # The shared planner still validates the tool selection and schema.
        return text_frame(json.dumps({
            "intent": "ui_operation", "tools": ["smart_report"],
            "required_tools": ["smart_report"], "skills": [], "ml_task": None,
            "intent_frame": {"language": "en"},
        }))

    async def stream(_self, **kwargs):
        calls.append(kwargs["messages"])
        response = (
            text_frame("Your private report draft is ready.")
            if any(message["role"] == "tool" for message in kwargs["messages"])
            else tool_call_frame("report-create-" + thread_id, name="smart_report", arguments={
                "action": "create", "spec": spec,
            })
        )
        if response.get("content"):
            yield "delta", response["content"]
        yield "message", response

    monkeypatch.setattr(AssistantProviderClient, "complete", complete)
    monkeypatch.setattr(AssistantProviderClient, "stream", stream)
    frontend = Path(__file__).resolve().parents[3] / "frontend"
    application = fixture.primary.http._transport.app
    server = uvicorn.Server(uvicorn.Config(application, lifespan="off", log_level="error"))
    process = None
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(15):
                while not server.started:
                    if task.done():
                        await task
                        pytest.fail("Browser acceptance server did not start")
                    await asyncio.sleep(0.05)
            process = await asyncio.create_subprocess_exec(
                "node", str(Path(__file__).with_name("report_browser.mjs")), cwd=frontend,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async with asyncio.timeout(240):
                output, _ = await process.communicate(json.dumps({
                    "mode": "prompt", "agent_name": fixture.agent["name"],
                    "api": f"http://127.0.0.1:{sock.getsockname()[1]}",
                    "token": fixture.primary.http.headers["Authorization"].removeprefix("Bearer "),
                    "origin": {"agent_id": agent_id, "thread_id": thread_id},
                    "screenshot": str(tmp_path / "report-stream.png"),
                }).encode())
            assert process.returncode == 0, f"{output.decode()}\nProvider streams: {len(calls)}"
            result = json.loads(output.decode().strip().splitlines()[-1])
            assert result["status"] == "passed"
            report_id = result["report_id"]
            report = await fixture.primary.request("GET", f"agents/studio/reports/{report_id}")
            assert report["published_revision"] == 0
            assert len(calls) == 2
            detail = await fixture.primary.request("GET", f"agents/{agent_id}/threads/{thread_id}")
            blocks = [step for message in detail["messages"] for step in message["steps"]
                      if step.get("kind") == "smart_report"]
            assert len(blocks) == 1 and blocks[0]["report_id"] == report_id
            assert not {"rows", "spec", "sources", "filters"} & blocks[0].keys()
        finally:
            if process is not None and process.returncode is None:
                process.terminate()
                await process.wait()
            server.should_exit = True
            await task
            # This fixture principal owns only this test's reports, even on assertion failure.
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_SMART_REPORT_EVENTS WHERE owner_name=%s",
                [fixture.principal],
            )
