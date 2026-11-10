"""M0 spike: verify OpenSandbox + the Nova notebook runtime image end to end.

Run from backend/ with the local OpenSandbox server up (docker compose service
``opensandbox-server``)::

    OPENSANDBOX_API_KEY=... uv run --no-sync python scripts/notebook_runtime_spike.py

It creates three sandboxes at once, checks a persistent Python context,
streaming, rich display bundles, interrupt, metrics and isolation, then kills
every sandbox it created. Findings are recorded in docs/testing/notebook/.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import time
from datetime import timedelta
from typing import Any

from code_interpreter import CodeInterpreter, SupportedLanguage
from code_interpreter.adapters.converter.code_execution_converter import (
    CodeExecutionConverter,
)
from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig

IMAGE = os.environ.get("NOTEBOOK_RUNTIME_IMAGE", "nova-notebook-runtime:dev")


def connection() -> ConnectionConfig:
    return ConnectionConfig(
        domain=os.environ.get("OPENSANDBOX_DOMAIN", "127.0.0.1:8090"),
        api_key=os.environ["OPENSANDBOX_API_KEY"],
        request_timeout=timedelta(seconds=60),
        use_server_proxy=os.environ.get("OPENSANDBOX_USE_SERVER_PROXY", "1") == "1",
    )


async def raw_run(interpreter: CodeInterpreter, context: Any, code: str) -> list[dict]:
    """Run code and keep every raw execd event, including full MIME bundles."""
    adapter = interpreter.codes
    request = CodeExecutionConverter.to_api_run_code_request(code, context)
    client = await adapter._get_sse_client()  # noqa: SLF001 - spike inspects raw events
    url = adapter._get_execd_url(adapter.RUN_CODE_PATH)  # noqa: SLF001
    events: list[dict] = []
    async with client.stream("POST", url, json=request) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                events.append({"unparsed": line[:200]})
    return events


def summarize(events: list[dict]) -> list[str]:
    out = []
    for event in events:
        kind = event.get("type")
        if kind == "result":
            results = event.get("results") or {}
            out.append(f"result mimes={sorted(results)}")
        elif kind in {"stdout", "stderr"}:
            out.append(f"{kind}: {event.get('text', '')[:60]!r}")
        elif kind == "error":
            err = event.get("error") or {}
            out.append(f"error {err.get('ename')}: {str(err.get('evalue'))[:60]}")
        else:
            out.append(f"{kind}")
    return out


async def start_one(label: str) -> tuple[Sandbox, CodeInterpreter, Any, float]:
    started = time.monotonic()
    sandbox = await Sandbox.create(
        IMAGE,
        connection_config=connection(),
        entrypoint=["/opt/nova/entrypoint.sh"],
        env={"JUPYTER_TOKEN": secrets.token_urlsafe(24)},
        metadata={"nova.spike": label},
        resource={"cpu": "1", "memory": "1Gi"},
        timeout=timedelta(minutes=15),
        ready_timeout=timedelta(seconds=120),
    )
    try:
        interpreter = await CodeInterpreter.create(sandbox=sandbox)
        context = await interpreter.codes.create_context(SupportedLanguage.PYTHON)
    except BaseException:
        await sandbox.kill()
        raise
    return sandbox, interpreter, context, time.monotonic() - started


async def main() -> int:
    labels = ["a", "b", "c"]
    results = await asyncio.gather(*(start_one(x) for x in labels), return_exceptions=True)
    live = [r for r in results if not isinstance(r, BaseException)]
    for label, r in zip(labels, results, strict=True):
        if isinstance(r, BaseException):
            print(f"[{label}] create FAILED: {type(r).__name__}: {r}")
        else:
            print(f"[{label}] sandbox={r[0].id} context={r[2].id} ready_in={r[3]:.1f}s")
    if len(live) < 3:
        for sandbox, *_ in live:
            await sandbox.kill()
        return 1

    (sa, ia, ca, _), (sb, ib, cb, _), (sc, ic, cc, _) = live
    try:
        print("--- persistent context")
        print(summarize(await raw_run(ia, ca, "x = 41\nprint('set x')")))
        print(summarize(await raw_run(ia, ca, "x + 1")))
        print("--- isolation (b must not see x)")
        print(summarize(await raw_run(ib, cb, "print('x' in globals())")))
        print("--- duckdb default connection")
        print(summarize(await raw_run(ia, ca, (
            "import duckdb\n"
            "db = duckdb.connect(':memory:')\n"
            "duckdb.set_default_connection(db)\n"
            "print(duckdb.sql('select 1 as ready').fetchall())"
        ))))
        print("--- rich display bundles")
        events = await raw_run(ia, ca, (
            "import matplotlib.pyplot as plt\n"
            "from IPython.display import display\n"
            "plt.plot([1,2,3]); plt.show()\n"
            "display({'application/vnd.nova.dataframe+json': {'k': 1},\n"
            "         'text/plain': 'df'}, raw=True)\n"
            "display({'text/html': '<b>hi</b>', 'text/plain': 'hi'}, raw=True)\n"
            "import pandas as pd\n"
            "pd.DataFrame({'a':[1,2]})"
        ))
        print(summarize(events))
        for event in events:
            if event.get("type") == "result":
                bundle = event.get("results") or {}
                sizes = {k: len(str(v)) for k, v in bundle.items()}
                print("   result mime sizes:", sizes)
        print("--- streaming order")
        print(summarize(await raw_run(ia, ca, (
            "import sys, time\n"
            "for i in range(3):\n"
            "    print('out', i); print('err', i, file=sys.stderr); time.sleep(0.2)"
        ))))
        print("--- error")
        print(summarize(await raw_run(ia, ca, "1/0")))
        print("--- interrupt (execution id from the init event)")
        run_task = asyncio.create_task(raw_run(ic, cc, (
            "import time\nfor i in range(60):\n    print(i, flush=True); time.sleep(0.5)"
        )))
        await asyncio.sleep(2.5)
        probe = await raw_run(ic, cc, "1")  # busy: shows the kernel is still running
        print("   while running:", summarize(probe))
        run_task.cancel()
        print("--- metrics")
        metrics = await sa.get_metrics()
        print("  ", metrics)
        print("--- network: can a sandbox reach the host proxy port?")
        print(summarize(await raw_run(ia, ca, (
            "import socket\n"
            "s=socket.socket(); s.settimeout(3)\n"
            "try:\n"
            "    s.connect(('host.docker.internal', 4406)); print('proxy reachable')\n"
            "except Exception as e:\n"
            "    print('proxy unreachable', type(e).__name__, e)\n"
        ))))
    finally:
        for sandbox in (sa, sb, sc):
            await sandbox.kill()
            print("killed", sandbox.id)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
