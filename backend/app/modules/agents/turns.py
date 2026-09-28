"""Run one Studio agent turn outside a chat request (automations, deep research).

This is the same bounded loop the Studio chat uses, assembled from the agent's
configuration. Callers decide the identity (``user``) and what may be approved
(``resolve_consent``); the loop, its limits, and its evidence checks are not
changed here.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

Consent = Callable[[Any, str], Awaitable[bool]]


@dataclass
class TurnOutput:
    text: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


async def read_only_consent(_invocation: Any, classification: str) -> bool:
    """Unattended runs approve reading and nothing else."""
    return classification == "read_only"


async def run_agent_turn(
    agent: dict[str, Any],
    *,
    user: dict[str, Any],
    question: str,
    thread_id: str,
    resolve_consent: Consent = read_only_consent,
    title: str = "",
) -> TurnOutput:
    from app.modules.agents.semantic.access import bound_view_ids
    from app.modules.agents.service import agent_service, loop_limits
    from app.modules.assistant.context import ContextManager
    from app.modules.assistant.provider import assistant_provider
    from app.modules.assistant.service import AssistantLoop, LoopContext
    from app.modules.assistant.state import AssistantThread

    registry, system_prompt, seconds, token_budget = await agent_service.build_loop_inputs(agent)
    limits = loop_limits(agent)
    context = LoopContext(
        user_name=user["username"],
        user=dict(user),
        thread_id=thread_id,
        agent_id=agent["agent_id"],
        agent_owner_name=agent.get("owner_name"),
        semantic_view_ids=bound_view_ids(agent),
        model_provider_id=agent.get("model_provider_id"),
        model_name=agent.get("model_name"),
        instructions=system_prompt,
    )
    context.steps = []
    loop = AssistantLoop(
        provider=assistant_provider,
        registry=registry,
        max_iterations=limits.max_iterations,
        time_budget_seconds=float(seconds),
        system_prompt=system_prompt,
        context_manager=ContextManager(token_budget=token_budget) if token_budget else None,
        max_calls_per_tool=limits.max_calls_per_tool,
        iterative=True,
    )
    thread = AssistantThread(thread_id=thread_id, user_name=user["username"], title=title)
    thread.consent.always_allow_read_only = True
    text: list[str] = []
    finish = None
    async for frame in loop.run(
        thread=thread, user_content=question, context=context, resolve_consent=resolve_consent,
    ):
        name = frame.split("\n", 1)[0][7:]
        if name not in {"text_delta", "done"}:
            continue
        try:
            payload = json.loads(frame.split("data: ", 1)[1])
        except (IndexError, ValueError):
            continue
        if name == "text_delta":
            text.append(str(payload.get("text") or ""))
        else:
            finish = payload.get("finish_reason")
    return TurnOutput(
        text="".join(text).strip(),
        steps=list(context.steps or []),
        tables=dict((context.verified_evidence or {}).get("tables") or {}),
        finish_reason=finish,
        usage=dict(context.usage or {}),
    )
