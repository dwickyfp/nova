"""What Neva remembers across conversations, per person and role.

Neva keeps durable facts the person stated in their own words ("our fiscal
year starts in April", "call the retail tables 'toko'") in the same memory
store Studio agents use, under the system agent id ``neva-assistant``. A fact
is scoped to the user and the role active when it was said: a different role
starts without it. Facts never come from data, tool results or the model,
credentials and questions are refused, every write is audited, and the person
can list and remove them. Memory is context, never permission or evidence.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The memory store's agent key for Neva; Neva has no agent record.
NEVA_MEMORY_ID = "neva-assistant"


def _role(user: dict[str, Any]) -> str:
    from app.modules.assistant.security import session_security

    return session_security(user).active_role


async def memory_note(user: dict[str, Any], question: str) -> str:
    """The remembered facts relevant to this question, framed as untrusted data."""
    from app.modules.agents.memory import (
        governed_memories,
        memory_prompt,
        memory_repository,
        select_relevant_memories,
    )

    try:
        memories = await memory_repository.list(
            user_name=user["username"], agent_id=NEVA_MEMORY_ID, role_name=_role(user),
        )
        if not memories:
            return ""
        selected = await select_relevant_memories(memories, question)
        return memory_prompt(await governed_memories(selected, user))
    except Exception as exc:  # noqa: BLE001 - a turn without memory still answers
        logger.warning("Neva memory unavailable: %s", type(exc).__name__)
        return ""


async def learn(
    user: dict[str, Any],
    *,
    thread_id: str,
    message: str,
    provider_id: str | None,
    model: str | None,
) -> int:
    """Keep durable facts from the person's own message; never raises.

    Questions, commands and anything credential-shaped are refused before any
    model call, so an ordinary question costs nothing.
    """
    from app.modules.agents.memory import remember_user_message
    from app.modules.assistant.provider import assistant_provider

    try:
        return await remember_user_message(
            user_name=user["username"],
            agent_id=NEVA_MEMORY_ID,
            role_name=_role(user),
            thread_id=thread_id,
            message=message,
            provider_id=provider_id,
            model=model,
            provider=assistant_provider,
            session_id=user.get("session_id"),
        )
    except Exception as exc:  # noqa: BLE001 - learning is best effort
        logger.info("Neva memory not updated: %s", type(exc).__name__)
        return 0
