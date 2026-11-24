"""Create and remove the disposable fixture agent for MCP and skill turns.

    uv run --script tests/fixtures/mcp/nova_test_tools.py --port 8299   # beside Nova
    uv run python -m tests.benchmark.studio_capability.fixtures setup
    uv run python -m tests.benchmark.studio_capability.fixtures teardown

``setup`` registers the deterministic MCP test service (an administrator
action), uploads the ``monthly-revenue-review`` skill as the benchmark user,
and creates an agent owned by that user. The agent copies the benchmark
agent's model, data bindings and tools, then adds the MCP tools and the skill;
the user's role is granted and verified. ``teardown`` deletes all three. The
ids are kept in ``<out>/fixtures.json`` so an interrupted run can still clean up.
Corpus units with ``"agent": "fixture"`` run on this agent; the runner maps
``mcp:<tool>`` in their expectations to the registered tool name.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from tests.benchmark.studio_capability import settings
from tests.benchmark.studio_capability.client import StudioClient, StudioError

REPO_BACKEND = Path(__file__).resolve().parents[3]
SKILL_FILE = REPO_BACKEND / "tests/fixtures/skills/monthly-revenue-review/SKILL.md"
SKILL_NAME = "monthly-revenue-review"
MCP_TOOLS = ("fx_convert", "get_store_targets", "fail_on_purpose", "record_target_note")
COPIED = ("description", "database_name", "schema_name", "model_provider_id", "model_name",
          "instructions_response", "instructions_orchestration", "response_style",
          "budget_seconds", "budget_tokens", "budget_profile", "semantic_view_ids",
          "resource_bindings")


def state_path(out: Path) -> Path:
    return out / "fixtures.json"


def load(out: Path) -> dict[str, Any] | None:
    path = state_path(out)
    return json.loads(path.read_text()) if path.is_file() else None


def _save(out: Path, state: dict[str, Any]) -> None:
    state_path(out).write_text(json.dumps(state, indent=1))


async def _client(identity: settings.Identity) -> StudioClient:
    client = StudioClient(settings.api())
    await client.login(identity.username, identity.password, identity.role)
    return client


async def setup(out: Path) -> dict[str, Any]:
    if load(out):
        raise SystemExit(f"{state_path(out)} exists: run teardown first")
    endpoint = os.environ.get("NOVA_BENCH_MCP_URL", "http://127.0.0.1:8299/mcp")
    stamp = time.strftime("%Y%m%d%H%M%S")
    state: dict[str, Any] = {"stamp": stamp}
    admin = await _client(settings.admin())
    user = await _client(settings.user())
    try:
        server = await admin.request("POST", "agents/mcp-servers", {
            "name": f"Bench target service {stamp}", "transport": "http",
            "endpoint": endpoint, "description": "Disposable benchmark fixture"})
        state["server_id"] = server["server_id"]
        _save(out, state)
        found = await admin.request("POST", f"agents/mcp-servers/{server['server_id']}/discover")
        if not found.get("ok"):
            raise StudioError(f"MCP discovery failed: {found.get('status')}")
        tools = (await admin.request("GET", "agents/tools"))["tools"]
        ids = {t["name"]: t["tool_id"] for t in tools
               if t.get("source") == f"mcp:{server['server_id']}"}
        missing = [name for name in MCP_TOOLS if name not in ids]
        if missing:
            raise StudioError(f"MCP tools not discovered: {missing}")
        skill = await user.request("POST", "agents/studio/skills",
                                   {"document": SKILL_FILE.read_text()})
        state["skill_id"] = skill["skill_id"]
        _save(out, state)
        base = await user.agent_by_name(settings.agent_name())
        detail = await user.request("GET", f"agents/{base['agent_id']}")
        body = {k: detail[k] for k in COPIED if detail.get(k) is not None}
        body.update({
            "name": f"Bench fixture {stamp}",
            "default_tools": list(detail.get("default_tools") or [])
            + [f"mcp:{ids[name]}" for name in MCP_TOOLS],
            "default_skills": [], "discoverable_skills": [SKILL_NAME],
            "visibility": "private",
        })
        agent = await user.request("POST", "agents", body)
        state["agent_id"] = agent["agent_id"]
        state["agent_name"] = agent["name"]
        state["model_name"] = agent.get("model_name")
        _save(out, state)
        role = user.role
        await user.request("POST", f"agents/{agent['agent_id']}/access",
                           {"role_name": role, "grant_type": "USAGE"})
        verified = await user.request("POST", f"agents/{agent['agent_id']}/access/verify",
                                      {"role_name": role})
        if not verified.get("all_granted"):
            raise StudioError("The fixture agent's access did not verify for the role")
        # Tool part names for the MCP tools (``mcp_<tool id without dashes>``).
        state["aliases"] = {f"mcp:{name}": "mcp_" + str(ids[name]).replace("-", "")
                            for name in MCP_TOOLS}
        _save(out, state)
        return state
    finally:
        await admin.close()
        await user.close()


async def teardown(out: Path) -> list[str]:
    state = load(out)
    if not state:
        return ["nothing to remove"]
    done: list[str] = []
    user = await _client(settings.user())
    admin = await _client(settings.admin())
    try:
        for key, client, path in (("agent_id", user, "agents/{}"),
                                  ("skill_id", user, "agents/skills/{}"),
                                  ("server_id", admin, "agents/mcp-servers/{}")):
            if not state.get(key):
                continue
            try:
                await client.request("DELETE", path.format(state[key]))
                done.append(f"deleted {key}")
            except StudioError as exc:
                done.append(f"{key}: {exc}")
                if exc.status != 404:
                    continue
            state.pop(key)
            _save(out, state)
    finally:
        await user.close()
        await admin.close()
    if not any(k in state for k in ("agent_id", "skill_id", "server_id")):
        state_path(out).rename(state_path(out).with_suffix(".removed.json"))
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["setup", "teardown"])
    parser.add_argument("--out", help="results directory outside the repository")
    args = parser.parse_args()
    out = settings.out_dir(args.out)
    if args.action == "setup":
        state = asyncio.run(setup(out))
        print(json.dumps({k: v for k, v in state.items() if k != "aliases"}, indent=1))
    else:
        print("\n".join(asyncio.run(teardown(out))))


if __name__ == "__main__":
    main()
