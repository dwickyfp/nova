"""Agent Studio service — compose an agent into a runnable assistant loop.

This module is the seam between the Phase 12 configuration (agents, semantic
models, skills) and the bounded intelligence loop. It builds the structural tool
registry and compact compiled context from agent configuration; the loop then
applies per-turn routing and gating.

The semantic-model grounding is injected into the tools' context (later stages),
not into the prompt as free text, so the model reasons over structured metadata.
"""

from __future__ import annotations

import logging
from typing import Any

from app.modules.agents.prompt import build_system_prompt
from app.modules.agents.registry import add_custom_tools, add_mcp_tools, build_registry
from app.modules.agents.repository import agent_repository
from app.modules.assistant.intelligence import SkillDefinition
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

        Default skills are loaded into trusted task-procedure context here.
        Discoverable skills stay out of the prompt until ``SkillRouter`` selects
        one for a turn. The global catalog is never dumped into model context.

        ``token_budget`` is the agent's context-window budget (NOVA-124), or
        ``None`` when the agent leaves it unset so the loop default applies. It
        is clamped to a sane range: a context budget below the skill prompt cost
        would make every turn overflow, and one above the model window defeats
        the purpose.
        """
        from app.modules.agents.skill_author import SKILL_AUTHOR_ID

        if agent.get("agent_id") == SKILL_AUTHOR_ID:
            from app.modules.agents.personal_skills import SKILL_AUTHORING_PROMPT

            prompt = build_system_prompt({"name": "Nova Studio"}, actual_tools=[])
            return ToolRegistry(), prompt + "\n\n" + SKILL_AUTHORING_PROMPT, 60, 16000

        registry = build_registry(agent)
        await add_custom_tools(registry, agent)
        await add_mcp_tools(registry, agent)

        requested_skills = [s for s in (agent.get("default_skills") or []) if s]
        discoverable_skills = [s for s in (agent.get("discoverable_skills") or []) if s]
        # Every ML-capable agent gets the trusted ML procedure on demand. It is
        # discoverable rather than default, so non-ML turns pay no prompt cost.
        if (
            "ml_execute" in registry.names()
            and "native-ml" not in requested_skills
            and "native-ml" not in discoverable_skills
        ):
            discoverable_skills.append("native-ml")
        skill_bodies: list[str] = []
        owner_name = str(agent.get("owner_name") or "")
        stored_skills = (
            await agent_repository.list_skills(owner_name=owner_name) if owner_name else []
        )
        user_skills = {str(skill["name"]): skill for skill in stored_skills}
        discoverable_skills = list(
            dict.fromkeys(
                [
                    *discoverable_skills,
                    *(
                        name
                        for name in user_skills
                        if name not in requested_skills and not skill_library.get(name)
                    ),
                ]
            )
        )
        configured_names = set(requested_skills) | set(discoverable_skills)
        for name in configured_names:
            platform = skill_library.get(name)
            if platform is not None:
                registry.skill_definitions[name] = SkillDefinition(
                    name=name,
                    summary=platform.summary,
                    triggers=platform.triggers,
                    body=platform.body,
                    trust_level="platform_skill",
                )
                continue
            user_skill = user_skills.get(name)
            if user_skill is not None:
                registry.skill_definitions[name] = SkillDefinition(
                    name=name,
                    summary=str(user_skill.get("description") or ""),
                    triggers=tuple(
                        word.lower() for word in str(user_skill.get("description") or name).split()
                    ),
                    body=str(user_skill.get("body") or ""),
                    trust_level="user_skill",
                )
        if requested_skills:
            missing = [s for s in requested_skills if s not in registry.skill_definitions]
            if missing:
                logger.warning(
                    "Agent %s selects unknown skill(s): %s",
                    agent.get("agent_id"),
                    ", ".join(missing),
                )
            skill_bodies = [
                registry.skill_definitions[name].prompt_body()
                for name in requested_skills
                if name in registry.skill_definitions
            ]

        registry.default_skills = tuple(requested_skills)
        registry.discoverable_skills = tuple(discoverable_skills)
        if registry.get("load_skill") is not None:
            from app.modules.agents.tools.load_personal_skill import PersonalSkillLoader

            registry.register(PersonalSkillLoader(owner_name, registry.skill_definitions))

        system_prompt = build_system_prompt(
            agent,
            skill_bodies=skill_bodies,
            actual_tools=registry.names(),
        )
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
