"""Probe the configured Sales agent's model with disposable business-rule memory."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import uuid4

from app.common.audit import write_audit_log
from app.core.database import db
from app.modules.agents.memory import (
    memory_prompt,
    memory_repository,
    remember_user_message,
    select_memories,
)
from app.modules.agents.repository import agent_repository
from app.modules.agents.service import agent_service
from app.modules.assistant.provider import (
    AssistantProviderClient,
    ProviderConfig,
    assistant_provider,
)

ANSWER_INSTRUCTIONS = (
    "Answer from supplied context. No tools are available in this benchmark. "
    "If business rules conflict, describe both definitions and their sources."
)


class CaptureExtractionUsage(AssistantProviderClient):
    def __init__(self) -> None:
        super().__init__()
        self.usage: dict | None = None

    async def resolve(
        self, *, provider_id: str | None = None, model: str | None = None
    ) -> ProviderConfig:
        return await assistant_provider.resolve(provider_id=provider_id, model=model)

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        provider: ProviderConfig,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        answer = await assistant_provider.complete(
            messages=messages,
            provider=provider,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
        )
        self.usage = answer.get("usage")
        return answer


async def main() -> None:
    await db.init_system_pool()
    cleanup_scope: tuple[str, str, str] | None = None
    try:
        rows = await db.execute_system(
            "SELECT agent_id, owner_name, name FROM NOVA_SYSTEM.CONFIG_AGENTS "
            "WHERE LOWER(name) LIKE %s ORDER BY updated_at DESC LIMIT 10",
            ["%sales%"],
        )
        if not rows["rows"]:
            print(json.dumps({"status": "no_sales_agent"}))
            return
        agent_id, owner_name, agent_name = rows["rows"][0]
        agent = await agent_repository.get_agent(agent_id, owner_name=owner_name)
        if agent is None:
            print(json.dumps({"status": "agent_unavailable"}))
            return
        await memory_repository.ensure_schema()
        _registry, agent_prompt, _budget, _token_budget = await agent_service.build_loop_inputs(
            agent
        )
        baseline_prompt = agent_prompt + "\n\n" + ANSWER_INSTRUCTIONS
        marker = uuid4().hex[:8]
        user_name = f"memory_benchmark_{marker}"
        role_name = "benchmark"
        cleanup_scope = (user_name, agent_id, role_name)
        thread_id = f"memory-benchmark-{marker}"
        rule = (
            "Dalam bisnis saya, omzet Sales adalah nilai invoice lunas "
            "dikurangi retur, tanpa PPN."
        )
        question = "Apa definisi omzet Sales yang pernah saya jelaskan?"
        config = await assistant_provider.resolve(
            provider_id=agent.get("model_provider_id"), model=agent.get("model_name")
        )
        print(
            json.dumps(
                {"status": "running", "agent": agent_name, "model": config.model, "marker": marker}
            ),
            flush=True,
        )
        start = time.perf_counter()
        baseline = await assistant_provider.complete(
            provider=config,
            messages=[
                {"role": "system", "content": baseline_prompt},
                {"role": "user", "content": question},
            ],
        )
        baseline_seconds = time.perf_counter() - start
        start = time.perf_counter()
        extraction_provider = CaptureExtractionUsage()
        count = await remember_user_message(
            user_name=user_name,
            agent_id=agent_id,
            role_name=role_name,
            thread_id=thread_id,
            message=rule,
            provider_id=config.provider_id,
            model=config.model,
            provider=extraction_provider,
        )
        extraction_seconds = time.perf_counter() - start
        extraction_usage = extraction_provider.usage
        memories = await memory_repository.list(
            user_name=user_name, agent_id=agent_id, role_name=role_name
        )
        selected = select_memories(memories, question)
        start = time.perf_counter()
        recalled = await assistant_provider.complete(
            provider=config,
            messages=[
                {
                    "role": "system",
                    "content": baseline_prompt + "\n\n" + memory_prompt(selected),
                },
                {"role": "user", "content": question},
            ],
        )
        recall_seconds = time.perf_counter() - start
        answer = str(recalled.get("content") or "")
        correction = (
            "Koreksi aturan bisnis saya: omzet Sales adalah nilai invoice lunas "
            "dikurangi retur dan diskon, tanpa PPN."
        )
        start = time.perf_counter()
        correction_provider = CaptureExtractionUsage()
        updated = await remember_user_message(
            user_name=user_name,
            agent_id=agent_id,
            role_name=role_name,
            thread_id=f"{thread_id}-new",
            message=correction,
            provider_id=config.provider_id,
            model=config.model,
            provider=correction_provider,
        )
        correction_seconds = time.perf_counter() - start
        revised_memories = await memory_repository.list(
            user_name=user_name, agent_id=agent_id, role_name=role_name
        )
        revised_prompt = memory_prompt(select_memories(revised_memories, question))
        revised = await assistant_provider.complete(
            provider=config,
            messages=[
                {"role": "system", "content": baseline_prompt + "\n\n" + revised_prompt},
                {"role": "user", "content": question},
            ],
        )
        revised_answer = str(revised.get("content") or "")
        print(
            json.dumps(
                {
                    "status": "complete",
                    "agent": agent_name,
                    "model": config.model,
                    "memory_written": count,
                    "memory_retrieved": len(selected),
                    "extracted_facts": [row["fact"] for row in memories],
                    "recall_correct": "retur" in answer.casefold() and "ppn" in answer.casefold(),
                    "correction_written": updated,
                    "correction_recalled": "diskon" in revised_answer.casefold(),
                    "memory_count_after_correction": len(revised_memories),
                    "correction_seconds": round(correction_seconds, 3),
                    "baseline_seconds": round(baseline_seconds, 3),
                    "extraction_seconds": round(extraction_seconds, 3),
                    "recall_seconds": round(recall_seconds, 3),
                    "baseline_answer": str(baseline.get("content") or "")[:800],
                    "recall_answer": answer[:800],
                    "revised_answer": revised_answer[:800],
                    "baseline_usage": baseline.get("usage"),
                    "extraction_usage": extraction_usage,
                    "correction_usage": correction_provider.usage,
                    "recall_usage": recalled.get("usage"),
                },
                ensure_ascii=False,
            )
        )
    finally:
        if cleanup_scope is not None:
            user_name, agent_id, role_name = cleanup_scope
            for row in await memory_repository.list(
                user_name=user_name, agent_id=agent_id, role_name=role_name
            ):
                await memory_repository.delete(
                    row["memory_id"],
                    user_name=user_name,
                    agent_id=agent_id,
                    role_name=role_name,
                )
                await write_audit_log(
                    event_type="AGENT_MEMORY",
                    user_name=user_name,
                    action="DELETE",
                    object_type="AGENT_MEMORY",
                    object_name=row["memory_id"],
                    status="SUCCESS",
                    active_role=role_name,
                )
        await db.close_system_pool()


if __name__ == "__main__":
    asyncio.run(main())
