"""Semantic-grounded, role-scoped delegation decisions for Auto."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from app.modules.agents.access import has_verified_access
from app.modules.agents.capabilities import CapabilityManifest, capability_repository
from app.modules.agents.repository import AgentMetadataUnavailable, agent_repository
from app.modules.agents.semantic.access import bound_view_ids
from app.modules.assistant.provider import AssistantProviderClient, assistant_provider
from app.modules.assistant.security import session_security

MAX_CANDIDATES = 16
MAX_CHILDREN = 4


class AgentDiscoveryUnavailable(RuntimeError):
    """The specialist list could not be verified from current metadata."""


@dataclass(frozen=True)
class Candidate:
    agent_id: str
    name: str
    manifest: CapabilityManifest
    metrics: tuple[dict[str, Any], ...]
    model_provider_id: str | None = None
    model_name: str | None = None

    def prompt_view(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.manifest.delegation_description,
            "capability_tags": self.manifest.capability_tags,
            "owns": self.manifest.owns,
            "good_for": self.manifest.good_for,
            "consult_when": self.manifest.consult_when,
            "not_primary_for": self.manifest.not_primary_for,
            "priority": self.manifest.priority,
        }


@dataclass(frozen=True)
class Assignment:
    agent_id: str
    objective: str
    context: str = ""


@dataclass(frozen=True)
class DelegationPlan:
    intent: str
    semantic_matches: tuple[dict[str, Any], ...]
    assignments: tuple[Assignment, ...]
    steering: tuple[dict[str, str], ...]
    plan_summary: str
    direct_answer: str = ""
    usage: dict[str, int] | None = None


def _terms(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold(), flags=re.UNICODE))


def semantic_matches(question: str, candidates: list[Candidate]) -> list[dict[str, Any]]:
    """Resolve only aliases declared by the semantic model; no domain keyword map."""
    lowered = question.casefold()
    words = _terms(question)
    matches: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        for metric in candidate.metrics:
            aliases = [metric.get("name"), *(metric.get("synonyms") or [])]
            found = next(
                (
                    str(a)
                    for a in aliases
                    if isinstance(a, str)
                    and a
                    and (a.casefold() in lowered if " " in a else a.casefold() in words)
                ),
                None,
            )
            if found:
                key = (candidate.agent_id, str(metric.get("name")))
                matches[key] = {
                    "agent_id": candidate.agent_id,
                    "metric": metric.get("name"),
                    "matched_alias": found,
                    "owner_domain": metric.get("owner_domain"),
                    "supporting_domains": metric.get("supporting_domains") or [],
                    "authority": metric.get("authority"),
                }
    return list(matches.values())[:32]


def rank_candidates(
    question: str, candidates: list[Candidate], matches: list[dict[str, Any]]
) -> list[Candidate]:
    """Retrieve compact candidates; semantic ownership outranks wording overlap."""
    query_terms = _terms(question)
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for match in matches:
        by_agent.setdefault(match["agent_id"], []).append(match)

    def score(candidate: Candidate) -> tuple[int, int, int, str]:
        manifest = candidate.manifest
        tags = {tag.casefold() for tag in manifest.capability_tags}
        owned = {metric.casefold() for metric in manifest.owns}
        semantic = 0
        for match in by_agent.get(candidate.agent_id, []):
            if (
                str(match.get("metric") or "").casefold() in owned
                or str(match.get("owner_domain") or "").casefold() in tags
            ):
                semantic = max(semantic, 3)
            elif any(
                str(domain).casefold() in tags for domain in match.get("supporting_domains") or []
            ):
                semantic = max(semantic, 2)
            else:
                semantic = max(semantic, 1)
        searchable = " ".join(
            [
                manifest.delegation_description,
                *manifest.capability_tags,
                *manifest.good_for,
                *manifest.consult_when,
                *manifest.owns,
            ]
        )
        overlap = len(query_terms & _terms(searchable))
        return (-semantic, -overlap, -manifest.priority, candidate.name.casefold())

    return sorted(candidates, key=score)


async def authorized_candidates(user: dict) -> list[Candidate]:
    from app.modules.intelligence.semantic_views import semantic_view_service

    role = session_security(user).active_role
    for attempt in range(3):
        try:
            own = await agent_repository.list_agents(owner_name=user["username"])
            shared = await agent_repository.list_shared_agents(role_name=role)
            result: list[Candidate] = []
            for agent in {a["agent_id"]: a for a in [*own, *shared]}.values():
                if not await has_verified_access(agent, role_name=role, user=user):
                    continue
                manifest = await capability_repository.get(agent)
                if not manifest.available_to_auto:
                    continue
                metrics: list[dict[str, Any]] = []
                for view_id in bound_view_ids(agent):
                    view = await semantic_view_service.get_active_for_agent(
                        view_id, user, agent_id=agent.get("agent_id")
                    )
                    if view:
                        metrics.extend((view.get("definition") or {}).get("metrics") or [])
                result.append(
                    Candidate(
                        agent_id=agent["agent_id"],
                        name=agent["name"],
                        manifest=manifest,
                        metrics=tuple(metrics),
                        model_provider_id=agent.get("model_provider_id"),
                        model_name=agent.get("model_name"),
                    )
                )
            if result or attempt == 2:
                return result
        except AgentMetadataUnavailable:
            if attempt == 2:
                raise AgentDiscoveryUnavailable(
                    "Agent discovery is temporarily unavailable"
                ) from None
        await asyncio.sleep(0.1 * (attempt + 1))
    raise AgentDiscoveryUnavailable("Agent discovery is temporarily unavailable")


def _parse_object(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Planner did not return an object")
    return value


class AutoPlanner:
    def __init__(self, provider: AssistantProviderClient = assistant_provider) -> None:
        self.provider = provider

    async def plan(
        self,
        *,
        question: str,
        user: dict,
        findings: list[dict[str, Any]] | None = None,
        existing_agent_ids: set[str] | None = None,
        provider_id: str | None = None,
        model: str | None = None,
    ) -> DelegationPlan:
        candidates = await authorized_candidates(user)
        matches = semantic_matches(question, candidates)
        ranked = rank_candidates(question, candidates, matches)
        compact = [candidate.prompt_view() for candidate in ranked[:MAX_CANDIDATES]]
        allowed = {item["agent_id"] for item in compact}
        if not compact:
            return DelegationPlan(
                intent="unroutable",
                semantic_matches=tuple(matches),
                assignments=(),
                steering=(),
                plan_summary="No accessible specialists.",
                direct_answer="No accessible specialist is configured for this question.",
            )
        instructions = (
            "You coordinate Nova specialists. Return one JSON object with keys: "
            "intent, plan_summary, assignments, steering, direct_answer. "
            "Each assignment has agent_id, objective, context. Steering has agent_id, message. "
            "Use only listed agent IDs. Assign the metric owner for authoritative facts; "
            "add contributors only if the question or findings require their evidence. "
            "A root-cause question may start with one owner scout. "
            "Do not choose by overlapping words alone. Never invent facts or agent IDs. "
            "Use an empty assignments list if specialists add no value. "
            "If existing agents already cover a task, send steering instead of spawning again. "
            f"At most {MAX_CHILDREN} assignments. Keep objectives bounded and factual."
        )
        prompt = json.dumps(
            {
                "question": question[:4000],
                "semantic_matches": matches,
                "agents": compact,
                "existing_agent_ids": sorted(existing_agent_ids or set()),
                "findings": (findings or [])[:12],
            },
            ensure_ascii=False,
        )
        preferred = next((item for item in ranked if item.model_provider_id), None)
        selected_provider = provider_id or (preferred.model_provider_id if preferred else None)
        selected_model = model
        if not selected_model and preferred and selected_provider == preferred.model_provider_id:
            selected_model = preferred.model_name
        provider_kwargs = {}
        if selected_provider:
            provider_kwargs["provider"] = await self.provider.resolve(
                provider_id=selected_provider, model=selected_model
            )
        answer = await self.provider.complete(
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            **provider_kwargs,
        )
        value = _parse_object(str(answer.get("content") or ""))
        existing = existing_agent_ids or set()
        assignments: list[Assignment] = []
        for item in value.get("assignments") or []:
            if not isinstance(item, dict) or item.get("agent_id") not in allowed:
                continue
            if item["agent_id"] in existing or any(
                a.agent_id == item["agent_id"] for a in assignments
            ):
                continue
            objective = str(item.get("objective") or "").strip()[:4000]
            if objective:
                assignments.append(
                    Assignment(
                        agent_id=item["agent_id"],
                        objective=objective,
                        context=str(item.get("context") or "")[:8000],
                    )
                )
            if len(assignments) >= MAX_CHILDREN:
                break
        steering = tuple(
            {"agent_id": str(item["agent_id"]), "message": str(item.get("message") or "")[:4000]}
            for item in value.get("steering") or []
            if isinstance(item, dict)
            and item.get("agent_id") in existing
            and str(item.get("message") or "").strip()
        )
        return DelegationPlan(
            intent=str(value.get("intent") or "unspecified")[:80],
            semantic_matches=tuple(matches),
            assignments=tuple(assignments),
            steering=steering,
            plan_summary=str(value.get("plan_summary") or "")[:1000],
            direct_answer=str(value.get("direct_answer") or "")[:4000],
            usage=answer.get("usage") if isinstance(answer.get("usage"), dict) else None,
        )


auto_planner = AutoPlanner()
