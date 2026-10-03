"""Opt-in scoring reads persisted traces and never invokes their tools."""

import json
from copy import deepcopy

from fastapi import HTTPException

from app.core.config import settings
from app.core.database import db
from app.modules.agents import quality
from app.modules.agents.quality_scoring import PromotionGates, aggregate_status
from app.modules.agents.releases import SCORER_SET_VERSION
from app.modules.intelligence.contracts import Scope, fingerprint
from app.modules.intelligence.engine_repository import metadata_lock
from app.observability.metrics import studio_operation


async def score_production(configuration_id: str, user: dict) -> dict:
    with studio_operation("quality", "score"):
        return await _score_production(configuration_id, user)


async def _score_production(configuration_id: str, user: dict) -> dict:
    if not settings.STUDIO_QUALITY_ENABLED:
        return {"status": "disabled"}
    from app.modules.agents.router import _require_agent
    from app.modules.intelligence.schedules import require_execution_binding

    await require_execution_binding(user)
    scope = Scope.from_user(user)
    async with metadata_lock(f"agent-quality:monitoring:{configuration_id}") as lock:
        configuration = await db.execute_system(
            "SELECT payload,revision FROM NOVA_SYSTEM.CONFIG_AGENT_QUALITY_MONITORING WHERE id=%s "
            "AND principal=%s AND active_role=%s AND security_context_version=%s "
            "ORDER BY revision DESC LIMIT 2",
            [configuration_id, scope.principal, scope.active_role, scope.security_context_version],
        )
        rows = configuration["rows"]
        if not rows:
            raise HTTPException(404, "Quality monitoring configuration is unavailable")
        if len(rows) > 1 and rows[0][1] == rows[1][1]:
            raise HTTPException(409, "Monitoring revision requires reconciliation")
        raw = rows[0][0]
        config = json.loads(raw) if isinstance(raw, str) else raw
        if config.get("enabled") is not True or config.get("schedule_status", "ready") != "ready":
            return {"status": "disabled"}
        bounds = quality.MonitoringRequest.model_validate(
            {
                key: config[key]
                for key in ("enabled", "sample_rate", "max_traces", "cadence_minutes")
            }
        )
        agent = await _require_agent(config["agent_id"], user)
        cases = deepcopy(await quality.records("cases", agent["agent_id"], user))
        traces = await db.execute_system(
            "SELECT message_id,steps FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES "
            "WHERE agent_id=%s AND user_name=%s AND role='assistant' "
            "AND get_json_string(CAST(security_context AS VARCHAR),'$.active_role')=%s "
            "AND get_json_int(CAST(security_context AS VARCHAR),'$.security_context_version')=%s "
            "ORDER BY created_at DESC LIMIT %s",
            [
                agent["agent_id"],
                scope.principal,
                scope.active_role,
                scope.security_context_version,
                bounds.max_traces,
            ],
        )
        scored = 0
        for message_id, raw_steps in traces["rows"][: bounds.max_traces]:
            if int(fingerprint(message_id)[:8], 16) / 0x100000000 >= bounds.sample_rate:
                continue
            if not await lock.renew():
                raise HTTPException(409, "Quality metadata lease expired")
            await require_execution_binding(user)
            try:
                steps = json.loads(raw_steps) if isinstance(raw_steps, str) else raw_steps
            except (ValueError, TypeError):
                steps = []
            steps = steps if isinstance(steps, list) else []
            assessment = next(
                (
                    step
                    for step in reversed(steps)
                    if isinstance(step, dict) and step.get("kind") == "quality_observation"
                ),
                {},
            )
            applicable = [
                case
                for case in cases
                if case.get("production_match") == "all_traces"
                or fingerprint(case["prompt"]) == assessment.get("prompt_digest")
            ]
            if not applicable:
                continue
            identifier = fingerprint(
                [
                    configuration_id,
                    config["revision"],
                    scope.model_dump(exclude={"session_id"}),
                    message_id,
                    SCORER_SET_VERSION,
                    [(case["id"], case["revision"]) for case in applicable],
                ]
            )
            async with metadata_lock(f"agent-quality-production:{identifier}"):
                if await quality.records("runs", agent["agent_id"], user, identifier):
                    continue
                trace = quality.observed_trace(steps)
                results = [
                    quality.case_result(case, trace, message_id, PromotionGates())
                    for case in applicable
                ]
                run = await quality.save_record(
                    "runs",
                    {
                        "id": identifier,
                        "agent_id": agent["agent_id"],
                        "source": "production",
                        "source_message_id": message_id,
                        "configuration_revision": config["revision"],
                        "version_id": assessment.get("version_id"),
                        "manifest_id": assessment.get("manifest_id"),
                        "manifest_fingerprint": assessment.get("manifest_fingerprint"),
                        "cases": applicable,
                        "case_fingerprint": fingerprint(applicable),
                        "results": results,
                        "scorer_set_version": SCORER_SET_VERSION,
                        "promotion_eligible": False,
                        "gate_results": quality.gate_results(applicable, results, PromotionGates()),
                        "status": aggregate_status([result["status"] for result in results]),
                    },
                    user,
                )
                if run["status"] != "passed" or any(
                    score["status"] != "pass"
                    for result in run["results"]
                    for score in [*result.get("scores", []), *result.get("budget_scores", [])]
                ):
                    await quality.feedback_proposal(agent["agent_id"], message_id, user, run)
                scored += 1
        return {"status": "complete", "scored": scored}
