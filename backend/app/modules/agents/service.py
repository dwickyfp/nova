"""Agent Studio service — compose an agent into a runnable assistant loop.

This module is the seam between the Phase 12 configuration (agents, semantic
models, skills) and the Phase 10 bounded loop. It does not modify the loop: it
builds the two things the loop takes as input — a tool registry and a system
prompt — from an agent's configuration, then hands off.

The semantic-model grounding is injected into the tools' context (later stages),
not into the prompt as free text, so the model reasons over structured metadata.
"""

from __future__ import annotations

import logging
from typing import Any

from app.modules.agents.prompt import build_system_prompt
from app.modules.agents.registry import add_custom_tools, build_registry
from app.modules.agents.repository import agent_repository
from app.modules.assistant.skill_registry import skill_library
from app.modules.assistant.tools import ToolRegistry

logger = logging.getLogger(__name__)

#: Defaults when an agent leaves a budget unset. They mirror the loop's own
#: defaults (NOVA-61 §9) so an unconfigured agent is no less bounded.
DEFAULT_BUDGET_SECONDS = 60
MAX_BUDGET_SECONDS = 3600

#: Bounds for an agent's context token budget (NOVA-124). A token budget of zero
#: or less means "use the loop default"; it is clamped to a sane ceiling so a
#: misconfigured agent cannot ask for an unbounded request.
MIN_CONTEXT_TOKEN_BUDGET = 1_000
MAX_CONTEXT_TOKEN_BUDGET = 120_000


class AgentNotFoundError(LookupError):
    """The agent does not exist or does not belong to the caller."""


class AgentService:
    """Loads an agent and assembles its loop inputs."""

    async def load(self, agent_id: str, *, owner_name: str) -> dict[str, Any]:
        agent = await agent_repository.get_agent(agent_id, owner_name=owner_name)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        return agent

    async def build_loop_inputs(
        self, agent: dict[str, Any]
    ) -> tuple[ToolRegistry, str, int, int | None]:
        """Return ``(registry, system_prompt, time_budget_seconds, token_budget)``.

        The skill catalog is the *default* library plus any of the agent's
        selected skills. User-defined skills (the ``CONFIG_AGENT_SKILLS`` table)
        are merged in a later stage (N12-G1); for now the catalog is the packaged
        library, and a selected-but-absent skill is reported as a warning rather
        than failing the turn.

        ``token_budget`` is the agent's context-window budget (NOVA-124), or
        ``None`` when the agent leaves it unset so the loop default applies. It
        is clamped to a sane range: a context budget below the skill prompt cost
        would make every turn overflow, and one above the model window defeats
        the purpose.
        """
        registry = build_registry(agent)
        await add_custom_tools(registry, agent)

        catalog = skill_library.catalog_prompt()
        requested_skills = [s for s in (agent.get("default_skills") or []) if s]
        if requested_skills:
            known = set(skill_library.names())
            missing = [s for s in requested_skills if s not in known]
            if missing:
                logger.warning(
                    "Agent %s selects unknown skill(s): %s",
                    agent.get("agent_id"),
                    ", ".join(missing),
                )

        system_prompt = build_system_prompt(agent, skill_catalog=catalog)
        budget = _clamp_budget(agent.get("budget_seconds"))
        token_budget = _clamp_token_budget(agent.get("budget_tokens"))
        return registry, system_prompt, budget, token_budget


def _clamp_budget(seconds: Any) -> int:
    if not isinstance(seconds, int) or seconds <= 0:
        return DEFAULT_BUDGET_SECONDS
    return min(seconds, MAX_BUDGET_SECONDS)


def _clamp_token_budget(tokens: Any) -> int | None:
    """Clamp an agent's context token budget, or ``None`` to use the default.

    An unset or non-positive value yields ``None`` (the loop's default), so an
    agent created before this field existed behaves exactly as before.
    """
    if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens <= 0:
        return None
    return max(MIN_CONTEXT_TOKEN_BUDGET, min(tokens, MAX_CONTEXT_TOKEN_BUDGET))


#: Process-wide service.
agent_service = AgentService()
