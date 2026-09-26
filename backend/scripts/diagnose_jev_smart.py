"""Trace access checks and provider timing for one development Smart trajectory."""

import argparse
import asyncio
import json
import time
from unittest.mock import patch

from tests.benchmark.jev_multidomain.environment import ARTIFACTS, configure_local_services
from tests.benchmark.jev_multidomain.runner import ACTIVE, LiveBenchmark


async def main(args) -> None:
    configure_local_services()
    from app.core.database import db
    from app.core.redis import session_store
    from app.modules.agents import harness_worker
    from app.modules.agents.access import access_fingerprint, verify_access
    from app.modules.agents.repository import agent_repository
    from app.modules.assistant.provider import AssistantProviderClient

    original_access = harness_worker.has_verified_access
    original_complete = AssistantProviderClient.complete
    original_stream = AssistantProviderClient.stream

    async def access(agent, *, role_name, user):
        start = time.monotonic()
        allowed = await original_access(agent, role_name=role_name, user=user)
        item = {"allowed": allowed, "latency_seconds": time.monotonic() - start}
        if not allowed:
            grants = await agent_repository.list_agent_roles(
                agent["agent_id"], owner_name=agent["owner_name"]
            )
            grant = next((g for g in grants if g["role_name"] == role_name), {})
            item["fingerprint_matches_on_recheck"] = grant.get(
                "verified_fingerprint"
            ) == await access_fingerprint(agent)
            checks = await verify_access(
                agent=agent,
                role_name=role_name,
                username=user["username"],
                encrypted_password=user["encrypted_password"],
                session_id=user["session_id"],
            )
            item["denied_on_recheck"] = [vars(c) for c in checks if not c.granted]
        if ACTIVE.get() is not None:
            ACTIVE.get().setdefault("access_checks", []).append(item)
        return allowed

    def call_record(kwargs):
        provider = kwargs.get("provider")
        return {
            "model": getattr(provider, "model", None),
            "message_chars": len(json.dumps(kwargs.get("messages", []))),
            "tool_count": len(kwargs.get("tools") or []),
        }

    async def complete(instance, **kwargs):
        item = {"method": "complete", **call_record(kwargs)}
        start = time.monotonic()
        try:
            result = await original_complete(instance, **kwargs)
            item["content"] = result.get("content")
            item["tool_calls"] = result.get("tool_calls")
            item["usage"] = result.get("usage")
            return result
        finally:
            item["latency_seconds"] = time.monotonic() - start
            if ACTIVE.get() is not None:
                ACTIVE.get().setdefault("provider_calls", []).append(item)

    async def stream(instance, **kwargs):
        item = {"method": "stream", **call_record(kwargs)}
        start = time.monotonic()
        try:
            async for kind, payload in original_stream(instance, **kwargs):
                item.setdefault("first_event_seconds", time.monotonic() - start)
                if kind == "message":
                    item["content"] = payload.get("content")
                    item["tool_calls"] = payload.get("tool_calls")
                    item["usage"] = payload.get("usage")
                yield kind, payload
        finally:
            item["latency_seconds"] = time.monotonic() - start
            if ACTIVE.get() is not None:
                ACTIVE.get().setdefault("provider_calls", []).append(item)

    await db.init_system_pool()
    await session_store.init()
    try:
        cases = json.loads((ARTIFACTS / "ground_truth.json").read_text())["cases"]
        selected = [c for c in cases if c["id"] in args.ids.split(",")]
        with (
            patch.object(harness_worker, "has_verified_access", access),
            patch.object(AssistantProviderClient, "complete", complete),
            patch.object(AssistantProviderClient, "stream", stream),
        ):
            await LiveBenchmark("on").run(
                selected, ARTIFACTS / args.output, concurrency=args.concurrency
            )
    finally:
        await db.close_system_pool()
        await session_store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", default="explicit_finance-001")
    parser.add_argument("--output", default="smart-diagnostic.jsonl")
    parser.add_argument("--concurrency", type=int, default=1)
    asyncio.run(main(parser.parse_args()))
