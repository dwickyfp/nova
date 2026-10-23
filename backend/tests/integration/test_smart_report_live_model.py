"""Opt-in external model acceptance through the actual Reports UI and governed engine."""

import asyncio
import json
import os
import socket
from pathlib import Path

import pytest
import uvicorn

from app.core.database import db
from app.modules.assistant.provider import assistant_provider
from app.modules.smart_reports.repository import report_repository
from tests.integration.test_governed_studio_ranger import governed as governed

pytestmark = [
    pytest.mark.engine,
    pytest.mark.skipif(
        os.getenv("NOVA_GOVERNED_STUDIO_RANGER_ACCEPTANCE") != "1"
        or os.getenv("NOVA_REPORT_LLM_ACCEPTANCE") != "1",
        reason="Requires explicit live-provider acceptance on the existing governed stack",
    ),
]


async def test_live_model_report_planning_through_library_ui(governed, tmp_path, monkeypatch):
    fixture = governed
    await report_repository.ensure_schema()
    provider = await assistant_provider.resolve()
    from app.modules.assistant.provider import AssistantProviderClient

    complete = AssistantProviderClient.complete

    async def observed_complete(self, **kwargs):
        result = await complete(self, **kwargs)
        (tmp_path / "model-response.json").write_text(json.dumps(result))
        return result

    monkeypatch.setattr(AssistantProviderClient, "complete", observed_complete)
    server = uvicorn.Server(
        uvicorn.Config(fixture.primary.http._transport.app, lifespan="off", log_level="error")
    )
    process = None
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(15):
                while not server.started:
                    if serving.done():
                        await serving
                    await asyncio.sleep(0.05)
            process = await asyncio.create_subprocess_exec(
                "node",
                str(Path(__file__).with_name("report_browser.mjs")),
                cwd=Path(__file__).resolve().parents[3] / "frontend",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async with asyncio.timeout(240):
                output, _ = await process.communicate(
                    json.dumps(
                        {
                            "mode": "live-plan",
                            "api": f"http://127.0.0.1:{sock.getsockname()[1]}",
                            "token": fixture.primary.http.headers["Authorization"].removeprefix(
                                "Bearer "
                            ),
                            "screenshot": str(tmp_path / "report-live-model.png"),
                        }
                    ).encode()
                )
            assert process.returncode == 0, output.decode()
            result = json.loads(output.decode().strip().splitlines()[-1])
            report = await fixture.primary.request(
                "GET", f"agents/studio/reports/{result['report_id']}"
            )
            assert report["published_revision"] == 0
            (tmp_path / "live-model-result.json").write_text(
                json.dumps({**result, "model": provider.model})
            )
        finally:
            if process is not None and process.returncode is None:
                process.terminate()
                await process.wait()
            server.should_exit = True
            await serving
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_SMART_REPORT_EVENTS WHERE owner_name=%s",
                [fixture.principal],
            )
