"""Run the production Auto worker with a probe-only pause before one run executes.

The pause leaves the run claimed and heartbeating. A separate process can then
send SIGKILL to exercise durable lease recovery without calling an agent tool.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from app.agent_worker.__main__ import _run
from app.modules.agents.harness_worker import AgentHarnessWorker


async def main() -> None:
    kind = os.environ["NOVA_KILL_PROBE_KIND"]
    target = os.environ["NOVA_KILL_PROBE_ROOT_ID"]
    marker = Path(os.environ["NOVA_KILL_PROBE_MARKER"])
    if kind not in {"root", "child"}:
        raise ValueError("Probe kind must be root or child")

    if kind == "root":
        original = AgentHarnessWorker._coordinate

        async def gate(self, run, user, cancelled):
            if run["run_id"] != target:
                return await original(self, run, user, cancelled)
            marker.write_text(json.dumps({
                "run_id": run["run_id"],
                "lease_owner": run["lease_owner"],
                "generation": run["generation"],
            }))
            await asyncio.Future()

        AgentHarnessWorker._coordinate = gate
    else:
        original = AgentHarnessWorker._execute_child

        async def gate(self, run, user, cancelled):
            if run["root_run_id"] != target:
                return await original(self, run, user, cancelled)
            marker.write_text(json.dumps({
                "run_id": run["run_id"],
                "root_run_id": run["root_run_id"],
                "lease_owner": run["lease_owner"],
                "generation": run["generation"],
            }))
            await asyncio.Future()

        AgentHarnessWorker._execute_child = gate

    await _run()


if __name__ == "__main__":
    asyncio.run(main())
