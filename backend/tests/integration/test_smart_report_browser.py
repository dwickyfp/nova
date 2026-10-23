"""Full Studio route and report editor against real governed HTTP and Chromium."""

import asyncio
import json
import os
import socket
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn

from app.core.database import db
from app.integrations.ranger.schemas import RangerPolicy
from app.modules.assistant.repository import assistant_repository
from app.modules.smart_reports.content import public_report_block
from app.modules.smart_reports.repository import report_repository
from tests.integration.test_governed_studio_ranger import _eventually, _query
from tests.integration.test_governed_studio_ranger import governed as governed

pytestmark = [
    pytest.mark.engine,
    pytest.mark.skipif(
        os.getenv("NOVA_GOVERNED_STUDIO_RANGER_ACCEPTANCE") != "1",
        reason="Requires existing patched-FE/Ranger, frontend dependencies and Chromium",
    ),
]


async def test_live_browser_report_edit_preview_publish_and_responsive_shell(governed, tmp_path):
    fixture = governed
    await report_repository.ensure_schema()
    policy = deepcopy(next(p for p in fixture.policies if p["policyType"] == 2))
    for item in policy["rowFilterPolicyItems"]:
        if item["users"] == [fixture.principal]:
            item["rowFilterInfo"]["filterExpr"] = "city IN ('Jakarta', 'Bandung')"
    await fixture.ranger.put_policy(RangerPolicy.model_validate(policy))

    async def both_cities():
        result = await _query(fixture.primary, f"SELECT city FROM {fixture.database}.sales")
        return {row[0] for row in result.get("rows", [])} == {"Jakarta", "Bandung"}

    await _eventually(both_cities, "Fixture policy did not allow both report cities")
    origin = {"agent_id": fixture.agent["agent_id"], "thread_id": fixture.thread["thread_id"]}
    spec = {
        "title": "Browser revenue",
        "sources": [{"id": "sales", **fixture.semantic}],
        "filters": [
            {
                "id": "city",
                "label": "City",
                "source": "sales",
                "field": "city",
                "kind": "single",
                "default": {"kind": "single", "values": ["Jakarta"]},
            }
        ],
        "pages": [
            {
                "id": "overview",
                "title": "Overview",
                "sections": [
                    {
                        "id": "main",
                        "widgets": [
                            {
                                "id": "revenue",
                                "title": "Revenue",
                                "kind": "kpi",
                                "query": {"source": "sales", "metrics": ["revenue"]},
                            }
                        ],
                    }
                ],
            }
        ],
    }
    spec["pages"].append(
        {
            "id": "trend",
            "title": "City comparison",
            "sections": [
                {
                    "id": "comparison",
                    "widgets": [
                        {
                            "id": "city-chart",
                            "title": "Revenue by city",
                            "kind": "bar",
                            "query": {
                                "source": "sales",
                                "metrics": ["revenue"],
                                "dimensions": ["city"],
                            },
                            "encoding": {"x": "city", "y": ["revenue"]},
                            "drill_dimensions": ["ordered_at"],
                        }
                    ],
                }
            ],
        }
    )
    spec["interactions"] = [
        {
            "id": "city-selection",
            "source_widget": "city-chart",
            "field": "city",
            "targets": ["revenue"],
        }
    ]
    from app.modules.assistant.intelligent_ui import compose_block
    from app.modules.assistant.security import observation_context, session_security

    governed_rows = await _query(
        fixture.primary,
        f"SELECT city, SUM(amount) AS revenue FROM {fixture.database}.sales "
        "GROUP BY city ORDER BY city",
    )
    ui = await compose_block(
        'root = Col([Chart(@Evidence("sales"), "city", "revenue", "bar", '
        '"Verified city comparison"), Table(@Evidence("sales"), '
        '["city", "revenue"], "Verified city data")])',
        "Verified city comparison.",
        tables={"sales": governed_rows},
        business=True,
        question="Compare city revenue",
        claims=[],
        language="en",
    )
    ui_thread = await assistant_repository.create_thread(
        user_name=fixture.principal, title="Intelligence UI review", agent_id=origin["agent_id"]
    )
    await assistant_repository.append_message(
        ui_thread["thread_id"],
        user_name=fixture.principal,
        agent_id=origin["agent_id"],
        role="assistant",
        content="Verified city comparison.",
        security_context=observation_context(session_security(fixture.user)),
        steps=[{"kind": "ui", "content_index": 0, "content_id": uuid4().hex, **ui}],
    )
    created = await fixture.primary.request(
        "POST",
        "agents/studio/reports",
        {"operation_id": uuid4().hex, "spec": spec, "origin": origin},
    )
    report_id = created["report_id"]
    path = f"agents/studio/reports/{report_id}"
    await fixture.primary.request(
        "POST",
        f"{path}/drafts/{created['draft_id']}/publish",
        {
            "operation_id": uuid4().hex,
            "expected_revision": 1,
            "expected_published_revision": 0,
        },
    )
    await assistant_repository.append_message(
        origin["thread_id"],
        user_name=fixture.principal,
        agent_id=origin["agent_id"],
        role="assistant",
        content="Your revenue report is ready.",
        steps=[
            {
                "kind": "smart_report",
                "content_index": 0,
                "content_id": uuid4().hex,
                **public_report_block(
                    {"report_id": report_id, "published_revision": 1, "title": spec["title"]}
                ),
            }
        ],
    )
    artifact = await fixture.primary.request(
        "POST",
        "agents/studio/artifacts",
        {
            "title": "Legacy city table",
            "artifact_type": "table",
            "sql_text": (
                f"SELECT city, SUM(amount) AS revenue FROM {fixture.database}.sales GROUP BY city"
            ),
            "database_name": fixture.database,
            **origin,
        },
    )
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
                        pytest.fail("Browser acceptance HTTP server did not start")
                    await asyncio.sleep(0.05)
            process = await asyncio.create_subprocess_exec(
                "node",
                str(Path(__file__).with_name("report_browser.mjs")),
                cwd=frontend,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            payload = {
                "api": f"http://127.0.0.1:{sock.getsockname()[1]}",
                "token": fixture.primary.http.headers["Authorization"].removeprefix("Bearer "),
                "report_id": report_id,
                "origin": origin,
                "ui_thread_id": ui_thread["thread_id"],
                "artifact_id": artifact["artifact_id"],
                "screenshot": str(tmp_path / "report-browser.png"),
            }
            async with asyncio.timeout(240):
                output, _ = await process.communicate(json.dumps(payload).encode())
            assert process.returncode == 0, output.decode()
            assert '"status":"passed"' in output.decode()
            from app.modules.smart_reports.context import resolve
            from app.modules.smart_reports.schemas import ReportContextRef

            result = json.loads(output.decode().strip().splitlines()[-1])
            evidence = await resolve(
                ReportContextRef.model_validate(result["context_reference"]),
                fixture.user,
                origin["thread_id"],
            )
            assert evidence["filters"]["city"]["values"] == ["Bandung"]
            assert evidence["tables"][0]["rows"] == [["3960.00"]]
            assert (await fixture.primary.request("GET", path))["published_revision"] == 2
        finally:
            if process is not None and process.returncode is None:
                process.terminate()
                await process.wait()
            server.should_exit = True
            await task
            from app.modules.smart_reports.exports import store

            await fixture.primary.request(
                "DELETE", f"agents/studio/artifacts/{artifact['artifact_id']}"
            )

            for record_id in await report_repository.ids("export", report_id=report_id):
                record = await report_repository.read(record_id)
                for field in ("uri", "snapshot_uri"):
                    await asyncio.to_thread(store().delete, record["payload"][field])
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_SMART_REPORT_EVENTS WHERE report_id=%s", [report_id]
            )
